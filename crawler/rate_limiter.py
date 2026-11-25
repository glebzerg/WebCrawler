import asyncio
from collections.abc import Awaitable, Callable
from math import isfinite
from time import monotonic


class RateLimiter:
    def __init__(
        self,
        requests_per_second: float = 1.0,
        *,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not isfinite(requests_per_second) or requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive and finite")

        self._interval = 1.0 / requests_per_second
        if not isfinite(self._interval):
            raise ValueError("requests_per_second is too small for a finite interval")

        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_start: float | None = None

    async def acquire(self) -> None:
        """Wait for the next permitted start and reserve its time."""
        async with self._lock:
            now = self._clock()
            while self._next_start is not None and now < self._next_start:
                await self._sleep(self._next_start - now)
                now = self._clock()

            self._next_start = now + self._interval
