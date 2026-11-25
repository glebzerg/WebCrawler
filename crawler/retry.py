import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from aiohttp import ClientResponseError, ClientTimeout
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


@dataclass(frozen=True)
class RetryTimeoutPolicy:
    factor: float = 2.0
    max_multiplier: float = 3.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.factor) or self.factor < 1:
            raise ValueError("factor must be finite and at least one")
        if not math.isfinite(self.max_multiplier) or self.max_multiplier < 1:
            raise ValueError("max_multiplier must be finite and at least one")

    def for_attempt(self, base: ClientTimeout, attempt_number: int) -> ClientTimeout:
        """Scale the enabled limits from the original timeout for each attempt."""
        if not isinstance(attempt_number, int) or attempt_number < 1:
            raise ValueError("attempt_number must be a positive integer")
        if attempt_number == 1 or self.factor == 1 or self.max_multiplier == 1:
            return base

        try:
            multiplier = min(math.pow(self.factor, attempt_number - 1), self.max_multiplier)
        except OverflowError:
            multiplier = self.max_multiplier

        def scale(seconds: float | None) -> float | None:
            if seconds is None or seconds <= 0:
                return seconds
            return seconds * multiplier

        return ClientTimeout(
            total=scale(base.total),
            connect=scale(base.connect),
            sock_read=scale(base.sock_read),
            sock_connect=base.sock_connect,
            ceil_threshold=base.ceil_threshold,
        )


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
        timeout_policy: RetryTimeoutPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")

        self._max_retries = max_retries
        self._retry_on = (TransientError, NetworkError) if retry_on is None else tuple(retry_on)
        self._default_backoff = BackoffPolicy(initial_delay, backoff_factor, max_delay)
        self._backoff_by_error = dict(backoff_by_error or {})
        self._timeout_policy = timeout_policy if timeout_policy is not None else RetryTimeoutPolicy()
        self._sleep = sleep

    async def execute_with_retry[T, **P](
        self, operation: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs
    ) -> T:
        """Call the operation again for each retry and propagate the final failure."""

        async def attempt(_number: int) -> T:
            return await operation(*args, **kwargs)

        return await self._execute_attempts(attempt)

    async def execute_with_timeout[T](
        self,
        operation: Callable[[ClientTimeout], Awaitable[T]],
        base_timeout: ClientTimeout,
        *,
        on_retry: Callable[[Exception, float], None] | None = None,
    ) -> T:
        """Pass each attempt's timeout and report scheduled retries before waiting."""

        async def attempt(number: int) -> T:
            return await operation(self._timeout_policy.for_attempt(base_timeout, number))

        return await self._execute_attempts(attempt, on_retry=on_retry)

    async def _execute_attempts[T](
        self,
        attempt: Callable[[int], Awaitable[T]],
        *,
        on_retry: Callable[[Exception, float], None] | None = None,
    ) -> T:
        attempt_number = 1
        while True:
            try:
                return await attempt(attempt_number)
            except Exception as error:
                if attempt_number > self._max_retries:
                    raise
                classified = classify_error(error)
                if not (isinstance(error, self._retry_on) or isinstance(classified, self._retry_on)):
                    raise
                delay = self._backoff_for(error, classified).delay_for(attempt_number)
                delay = max(delay, _retry_after_delay(error))
                if on_retry is not None:
                    on_retry(error, delay)
                if delay > 0:
                    await self._sleep(delay)
                attempt_number += 1

    def _backoff_for(
        self, error: Exception, classified: Exception | None
    ) -> BackoffPolicy:
        # The first configured match wins, including matches on a crawler category.
        for error_type, policy in self._backoff_by_error.items():
            if isinstance(error, error_type) or isinstance(classified, error_type):
                return policy
        return self._default_backoff
