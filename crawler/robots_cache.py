import asyncio
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

from .robots_parser import RobotsParser


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
    def __init__(self, fetch_text: Callable[[str], Awaitable[str]]) -> None:
        self._fetch_text = fetch_text
        self._loads: dict[str, asyncio.Task[RobotsParser]] = {}

    async def get(self, url: str) -> RobotsParser:
        robots_url = _robots_url(url)
        task = self._loads.get(robots_url)
        if task is None:
            task = asyncio.create_task(self._load(robots_url))
            self._loads[robots_url] = task
            task.add_done_callback(lambda done: self._discard_failed(robots_url, done))

        return await asyncio.shield(task)

    async def close(self) -> None:
        pending = [task for task in self._loads.values() if not task.done()]
        self._loads.clear()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _load(self, robots_url: str) -> RobotsParser:
        return RobotsParser(await self._fetch_text(robots_url))

    def _discard_failed(self, url: str, task: asyncio.Task[RobotsParser]) -> None:
        if task.cancelled() or task.exception() is not None:
            if self._loads.get(url) is task:
                del self._loads[url]
