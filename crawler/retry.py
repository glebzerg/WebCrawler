from collections.abc import Awaitable, Callable, Sequence

from .errors import NetworkError, TransientError, classify_error


class RetryStrategy:
    def __init__(
        self,
        max_retries: int = 3,
        *,
        retry_on: Sequence[type[Exception]] | None = None,
    ) -> None:
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")

        self._max_retries = max_retries
        self._retry_on = (TransientError, NetworkError) if retry_on is None else tuple(retry_on)

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
                if not isinstance(error, self._retry_on):
                    classified = classify_error(error)
                    if not isinstance(classified, self._retry_on):
                        raise
                retries_left -= 1
