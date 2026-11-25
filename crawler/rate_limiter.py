import asyncio
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from math import isfinite
from random import random
from time import monotonic


class _StartSchedule:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.next_start: float | None = None


class RateLimiter:
    def __init__(
        self,
        requests_per_second: float | None = 1.0,
        per_domain: bool = True,
        *,
        min_delay: float = 0.0,
        jitter: float = 0.0,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_source: Callable[[], float] = random,
    ) -> None:
        self._interval = 0.0
        if requests_per_second is not None:
            if not isfinite(requests_per_second) or requests_per_second <= 0:
                raise ValueError("requests_per_second must be positive and finite")
            self._interval = 1.0 / requests_per_second
            if not isfinite(self._interval):
                raise ValueError("requests_per_second is too small for a finite interval")
        for name, value in (("min_delay", min_delay), ("jitter", jitter)):
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be non-negative and finite")

        self._clock = clock
        self._sleep = sleep
        self._random_source = random_source
        self._per_domain = per_domain
        self._min_delay = min_delay
        self._jitter = jitter
        self._schedules: dict[str | None, _StartSchedule] = {}

    async def acquire(self, domain: str | None = None) -> None:
        """Wait for the next permitted start and reserve its time."""
        host = domain.lower() if domain is not None else None
        rate_schedule = self._schedule_for(host if self._per_domain else None)
        interval = self._interval
        host_schedule = None
        if self._per_domain or host is None:
            interval = max(interval, self._min_delay)
        elif self._min_delay > 0:
            host_schedule = self._schedule_for(host)

        host_lock = host_schedule.lock if host_schedule is not None else nullcontext()
        async with host_lock:
            if host_schedule is not None:
                # Let other hosts use the shared schedule during this host-specific wait.
                await self._wait_for_start(host_schedule)

            async with rate_schedule.lock:
                now = await self._wait_for_start(rate_schedule)
                extra = self._jitter * self._random_source() if self._jitter else 0.0
                rate_schedule.next_start = now + (interval + extra)
                if host_schedule is not None:
                    host_schedule.next_start = now + (self._min_delay + extra)

    def _schedule_for(self, key: str | None) -> _StartSchedule:
        schedule = self._schedules.get(key)
        if schedule is None:
            schedule = _StartSchedule()
            self._schedules[key] = schedule
        return schedule

    async def _wait_for_start(self, schedule: _StartSchedule) -> float:
        now = self._clock()
        while schedule.next_start is not None and now < schedule.next_start:
            await self._sleep(schedule.next_start - now)
            now = self._clock()
        return now
