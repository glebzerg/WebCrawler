import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext


class SemaphoreManager:
    def __init__(self, max_concurrent: int, max_per_host: int | None = None) -> None:
        if not isinstance(max_concurrent, int) or max_concurrent < 1:
            raise ValueError("max_concurrent must be a positive integer")
        if max_per_host is not None and (
            not isinstance(max_per_host, int) or max_per_host < 1
        ):
            raise ValueError("max_per_host must be a positive integer or None")

        self._global_slots = asyncio.Semaphore(max_concurrent)
        self._max_per_host = max_per_host
        self._host_slots: dict[str, asyncio.Semaphore] = {}
        self._active_requests = 0

    @property
    def active_requests(self) -> int:
        return self._active_requests

    @asynccontextmanager
    async def acquire(self, host: str | None) -> AsyncIterator[None]:
        host_slots = None
        if host is not None and self._max_per_host is not None:
            host_slots = self._host_slots.get(host)
            if host_slots is None:
                host_slots = asyncio.Semaphore(self._max_per_host)
                self._host_slots[host] = host_slots

        host_context = host_slots if host_slots is not None else nullcontext()
        async with host_context, self._global_slots:
            self._active_requests += 1
            try:
                yield
            finally:
                self._active_requests -= 1
