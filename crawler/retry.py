import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from aiohttp import ClientResponseError
from aiohttp.hdrs import RETRY_AFTER

from .errors import NetworkError, TransientError, classify_error


def _retry_after_delay(error: Exception) -> float:
    """Return the server's minimum delay in seconds, or zero if unavailable."""
    response_error = error if isinstance(error, ClientResponseError) else error.__cause__
    if not isinstance(response_error, ClientResponseError) or response_error.headers is None:
        return 0.0

    value = response_error.headers.get(RETRY_AFTER, "").strip()
    if value.isascii() and value.isdecimal():
        seconds = float(value)
        return seconds if math.isfinite(seconds) else 0.0

    try:
        retry_at = parsedate_to_datetime(value)
    except (ValueError, OverflowError):
        return 0.0
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


@dataclass(frozen=True)
class BackoffPolicy:
    initial_delay: float = 1.0
    factor: float = 2.0
    max_delay: float = 30.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.initial_delay) or self.initial_delay < 0:
            raise ValueError("initial_delay must be finite and non-negative")
        if not math.isfinite(self.factor) or self.factor < 1:
            raise ValueError("factor must be finite and at least one")
        if not math.isfinite(self.max_delay) or self.max_delay < 0:
            raise ValueError("max_delay must be finite and non-negative")

    def delay_for(self, retry_number: int) -> float:
        """Return the delay in seconds for a retry numbered from one."""
        if not isinstance(retry_number, int) or retry_number < 1:
            raise ValueError("retry_number must be a positive integer")
        if self.initial_delay == 0 or self.initial_delay >= self.max_delay or self.factor == 1:
            return min(self.initial_delay, self.max_delay)

        exponent = retry_number - 1
        try:
            delay = self.initial_delay * self.factor ** exponent
        except OverflowError:
            # The power can overflow even when multiplying by the base would fit.
            log_initial = math.log(self.initial_delay)
            log_factor = math.log(self.factor)
            if exponent >= (math.log(self.max_delay) - log_initial) / log_factor:
                return self.max_delay
            delay = math.exp(log_initial + exponent * log_factor)
        return min(delay, self.max_delay)


class RetryStrategy:
    def __init__(
        self,
        max_retries: int = 3,
        *,
        retry_on: Sequence[type[Exception]] | None = None,
        initial_delay: float = 1.0,
        backoff_factor: float = 2.0,
        max_delay: float = 30.0,
        backoff_by_error: Mapping[type[Exception], BackoffPolicy] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")

        self._max_retries = max_retries
        self._retry_on = (TransientError, NetworkError) if retry_on is None else tuple(retry_on)
        self._default_backoff = BackoffPolicy(initial_delay, backoff_factor, max_delay)
        self._backoff_by_error = dict(backoff_by_error or {})
        self._sleep = sleep

    async def execute_with_retry[T, **P](
        self, operation: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs
    ) -> T:
        """Call the operation again for each retry and propagate the final failure."""
        retries_left = self._max_retries
        while True:
            try:
                return await operation(*args, **kwargs)
            except Exception as error:
                if retries_left == 0:
                    raise
                classified = classify_error(error)
                if not (isinstance(error, self._retry_on) or isinstance(classified, self._retry_on)):
                    raise
                retries_left -= 1
                retry_number = self._max_retries - retries_left
                delay = self._backoff_for(error, classified).delay_for(retry_number)
                delay = max(delay, _retry_after_delay(error))
                if delay > 0:
                    await self._sleep(delay)

    def _backoff_for(
        self, error: Exception, classified: Exception | None
    ) -> BackoffPolicy:
        # The first configured match wins, including matches on a crawler category.
        for error_type, policy in self._backoff_by_error.items():
            if isinstance(error, error_type) or isinstance(classified, error_type):
                return policy
        return self._default_backoff
