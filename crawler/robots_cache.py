import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientResponseError

from .robots_parser import RobotsParser


_UNAVAILABLE_RETRY_DELAY = 30.0


class RobotsUnavailableError(ClientError):
    def __init__(self, url: str, cause: Exception) -> None:
        self.url = url
        self.status = cause.status if isinstance(cause, ClientResponseError) else None
        reason = f"HTTP {self.status}" if self.status is not None else type(cause).__name__
        super().__init__(f"Cannot read robots.txt at {url} ({reason})")


@dataclass
class _Load:
    task: asyncio.Task[RobotsParser]
    retry_at: float | None = None


def _robots_url(url: str) -> str:
    address = urlsplit(url)
    hostname = address.hostname
    if address.scheme not in {"http", "https"} or not hostname:
        raise ValueError(f"Expected an HTTP(S) URL with a host: {url}")

    host = f"[{hostname}]" if ":" in hostname else hostname
    port = address.port
    default_port = 443 if address.scheme == "https" else 80
    if port is not None and port != default_port:
        host = f"{host}:{port}"

    return urlunsplit((address.scheme, host, "/robots.txt", "", ""))


class RobotsCache:
    def __init__(
        self,
        fetch_text: Callable[[str], Awaitable[str]],
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._fetch_text = fetch_text
        self._clock = clock
        self._loads: dict[str, _Load] = {}

    async def get(self, url: str) -> RobotsParser:
        robots_url = _robots_url(url)
        load = self._loads.get(robots_url)
        if load is None or (load.retry_at is not None and self._clock() >= load.retry_at):
            task = asyncio.create_task(self._load(robots_url))
            load = _Load(task)
            self._loads[robots_url] = load
            task.add_done_callback(lambda _: self._record_outcome(robots_url, load))

        return await asyncio.shield(load.task)

    async def close(self) -> None:
        pending = [load.task for load in self._loads.values() if not load.task.done()]
        self._loads.clear()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _load(self, robots_url: str) -> RobotsParser:
        try:
            text = await self._fetch_text(robots_url)
        except (ClientError, TimeoutError, UnicodeDecodeError) as error:
            if isinstance(error, ClientResponseError) and error.status == 404:
                return RobotsParser("")
            raise RobotsUnavailableError(robots_url, error) from error
        return RobotsParser(text)

    def _record_outcome(self, url: str, load: _Load) -> None:
        cancelled = load.task.cancelled()
        error = None if cancelled else load.task.exception()
        if self._loads.get(url) is not load:
            return
        if isinstance(error, RobotsUnavailableError):
            load.retry_at = self._clock() + _UNAVAILABLE_RETRY_DELAY
        elif cancelled or error is not None:
            del self._loads[url]
