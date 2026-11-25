import asyncio
from collections.abc import Awaitable, Callable
from math import isfinite
from time import monotonic


class _StartSchedule:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.next_start: float | None = None


class RateLimiter:
    def __init__(
        self,
        requests_per_second: float = 1.0,
        per_domain: bool = True,
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
        self._per_domain = per_domain
        self._schedules: dict[str | None, _StartSchedule] = {}

    async def acquire(self, domain: str | None = None) -> None:
        """Wait for the next permitted start and reserve its time."""
        key = domain.lower() if self._per_domain and domain is not None else None
        schedule = self._schedules.get(key)
        if schedule is None:
            schedule = _StartSchedule()
            self._schedules[key] = schedule

        async with schedule.lock:
            now = self._clock()
            while schedule.next_start is not None and now < schedule.next_start:
                await self._sleep(schedule.next_start - now)
                now = self._clock()

            schedule.next_start = now + self._interval
