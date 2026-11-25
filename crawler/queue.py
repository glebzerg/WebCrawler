from heapq import heappop, heappush
from itertools import count


class CrawlerQueue:
    def __init__(self) -> None:
        self._pending: list[tuple[int, int, str]] = []
        self._arrival_order = count()

    def add_url(self, url: str, priority: int = 0) -> None:
        entry = (-priority, next(self._arrival_order), url)
        heappush(self._pending, entry)

    async def get_next(self) -> str | None:
        if not self._pending:
            return None

        _, _, url = heappop(self._pending)
        return url
