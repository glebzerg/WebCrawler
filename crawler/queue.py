from heapq import heappop, heappush
from itertools import count

from .html_parser import ParsedPage
from .urls import url_key


class CrawlerQueue:
    def __init__(self) -> None:
        self._pending: list[tuple[int, int, int, str, str]] = []
        self._arrival_order = count()
        self._in_progress: set[str] = set()
        self.visited_urls: set[str] = set()
        self.processed_urls: dict[str, ParsedPage | None] = {}
        self.failed_urls: dict[str, str] = {}

    def add_url(self, url: str, priority: int = 0, *, depth: int = 0) -> None:
        key = url_key(url)
        if key in self.visited_urls:
            return

        entry = (depth, -priority, next(self._arrival_order), key, url)
        heappush(self._pending, entry)
        self.visited_urls.add(key)

    async def get_next(self) -> str | None:
        job = await self.get_next_job()
        return job[0] if job is not None else None

    def next_depth(self) -> int | None:
        return self._pending[0][0] if self._pending else None

    async def get_next_job(self, *, max_depth: int | None = None) -> tuple[str, int] | None:
        """Take the next job if its depth is within the requested level."""
        depth = self.next_depth()
        if depth is None or (max_depth is not None and depth > max_depth):
            return None

        depth, _, _, key, url = heappop(self._pending)
        self._in_progress.add(key)
        return url, depth

    def mark_processed(self, url: str, result: ParsedPage | None = None) -> None:
        key = self._finish(url)
        self.processed_urls[key] = result

    def mark_failed(self, url: str, error: str) -> None:
        key = self._finish(url)
        self.failed_urls[key] = error

    def get_stats(self) -> dict[str, int]:
        return {
            "queued": len(self._pending),
            "in_progress": len(self._in_progress),
            "processed": len(self.processed_urls),
            "failed": len(self.failed_urls),
            "visited": len(self.visited_urls),
        }

    def _finish(self, url: str) -> str:
        key = url_key(url)
        if key not in self._in_progress:
            raise ValueError(f"URL is not in progress: {url}")
        self._in_progress.remove(key)
        return key
