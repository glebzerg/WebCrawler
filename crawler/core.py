import asyncio
import logging
from collections.abc import Callable, Sequence
from fnmatch import fnmatchcase
from time import perf_counter
from urllib.parse import urlsplit

from aiohttp import (
    ClientConnectionError,
    ClientConnectorError,
    ClientError,
    ClientSession,
    ClientTimeout,
)

from .html_parser import HTMLParser, ParsedPage
from .queue import CrawlerQueue, QueueStats
from .rate_limiter import RateLimiter
from .semaphore_manager import SemaphoreManager
from .urls import url_key

logger = logging.getLogger(__name__)
_FETCH_ERRORS = (ClientError, TimeoutError, UnicodeDecodeError)


class CrawlProgress(QueueStats):
    pages_per_second: float


class FetchConnectionError(ClientConnectionError):
    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"Failed to connect to {url}")


class AsyncCrawler:
    def __init__(
        self,
        max_concurrent: int = 10,
        *,
        max_per_host: int | None = None,
        requests_per_second: float | None = None,
        per_domain: bool = True,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
    ) -> None:
        self._max_concurrent = max_concurrent
        self._semaphores = SemaphoreManager(max_concurrent, max_per_host)
        self._rate_limiter = (
            RateLimiter(requests_per_second, per_domain=per_domain)
            if requests_per_second is not None
            else None
        )
        self._timeout = ClientTimeout(
            total=5 * 60,
            connect=connect_timeout,
            sock_read=read_timeout,
        )
        self._session: ClientSession | None = None
        self._closed = False
        self.failed_urls: dict[str, Exception] = {}

    @property
    def active_requests(self) -> int:
        return self._semaphores.active_requests

    def _get_session(self) -> ClientSession:
        if self._closed:
            raise RuntimeError("AsyncCrawler is closed")

        if self._session is None:
            self._session = ClientSession(timeout=self._timeout)
        return self._session

    async def fetch_url(self, url: str) -> str:
        text, _ = await self._fetch_page(url)
        return text

    async def fetch_and_parse(self, url: str) -> ParsedPage:
        html, final_url = await self._fetch_page(url)
        page = await HTMLParser().parse_html(html, final_url)
        page["url"] = url
        return page

    async def _fetch_page(self, url: str) -> tuple[str, str]:
        try:
            host = urlsplit(url).hostname
        except ValueError:
            host = None

        async with self._semaphores.acquire(host):
            session = self._get_session()
            if self._rate_limiter is not None:
                await self._rate_limiter.acquire(host)
            logger.info(
                "Started fetching %s",
                url,
                extra={"event": "fetch_started", "url": url},
            )
            try:
                async with session.get(url) as response:
                    response.raise_for_status()
                    text = await response.text()
                    final_url = str(response.url)
            except Exception as error:
                error_type = type(error).__name__
                logger.warning(
                    "Failed to fetch %s (%s)",
                    url,
                    error_type,
                    extra={"event": "fetch_failed", "url": url, "error_type": error_type},
                )
                if isinstance(error, ClientConnectorError):
                    raise FetchConnectionError(url) from error
                raise

            logger.info(
                "Finished fetching %s",
                url,
                extra={"event": "fetch_succeeded", "url": url},
            )
            return text, final_url

    async def fetch_urls(self, urls: list[str]) -> dict[str, str]:
        """Fetch each unique URL and record failures for the completed batch."""
        batch_urls = list(dict.fromkeys(urls))
        outcomes = await asyncio.gather(
            *(self.fetch_url(url) for url in batch_urls),
            return_exceptions=True,
        )

        pages: dict[str, str] = {}
        failures: dict[str, Exception] = {}
        for url, outcome in zip(batch_urls, outcomes, strict=True):
            if isinstance(outcome, _FETCH_ERRORS):
                failures[url] = outcome
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                pages[url] = outcome

        self.failed_urls = failures
        return pages

    async def crawl(
        self,
        start_urls: list[str],
        max_pages: int = 100,
        *,
        max_depth: int | None = None,
        same_domain_only: bool = False,
        include_patterns: Sequence[str] = (),
        exclude_patterns: Sequence[str] = (),
        on_progress: Callable[[CrawlProgress], None] | None = None,
    ) -> dict[str, ParsedPage]:
        """Follow eligible links by depth until the page budget is used or the queue is empty."""
        if not isinstance(max_pages, int) or max_pages < 0:
            raise ValueError("max_pages must be a non-negative integer")
        if max_depth is not None and (not isinstance(max_depth, int) or max_depth < 0):
            raise ValueError("max_depth must be a non-negative integer or None")

        queue = CrawlerQueue()
        started_at = perf_counter()

        def report_progress() -> None:
            if on_progress is None:
                return
            stats = queue.get_stats()
            elapsed = perf_counter() - started_at
            completed = stats["processed"] + stats["failed"]
            rate = completed / elapsed if elapsed > 0 else 0.0
            on_progress({**stats, "pages_per_second": rate})

        if max_pages == 0:
            self.failed_urls = {}
            report_progress()
            return {}

        allowed_hosts = (
            {urlsplit(url).hostname for url in start_urls} - {None}
            if same_domain_only
            else None
        )

        def should_enqueue(url: str) -> bool:
            if allowed_hosts is not None and urlsplit(url).hostname not in allowed_hosts:
                return False
            if not include_patterns and not exclude_patterns:
                return True

            key = url_key(url)
            if any(fnmatchcase(key, pattern) for pattern in exclude_patterns):
                return False
            return not include_patterns or any(
                fnmatchcase(key, pattern) for pattern in include_patterns
            )

        for url in start_urls:
            if should_enqueue(url):
                queue.add_url(url)
        report_progress()

        failures: dict[str, Exception] = {}
        remaining_pages = max_pages

        async def crawl_level(depth: int) -> None:
            nonlocal remaining_pages
            while remaining_pages > 0:
                job = await queue.get_next_job(max_depth=depth)
                if job is None:
                    return
                # Dequeuing does not suspend, so the slot is reserved before the next worker runs.
                remaining_pages -= 1
                url, _ = job

                try:
                    page = await self.fetch_and_parse(url)
                except _FETCH_ERRORS as error:
                    queue.mark_failed(url, str(error))
                    failures[url] = error
                    report_progress()
                    continue

                queue.mark_processed(url, page)
                if max_depth is None or depth < max_depth:
                    for link in page["links"]:
                        if should_enqueue(link):
                            queue.add_url(link, depth=depth + 1)
                report_progress()

        while remaining_pages > 0 and (depth := queue.next_depth()) is not None:
            worker_count = min(
                self._max_concurrent, queue.get_stats()["queued"], remaining_pages
            )
            async with asyncio.TaskGroup() as workers:
                for _ in range(worker_count):
                    workers.create_task(crawl_level(depth))

        self.failed_urls = failures
        return {
            page["url"]: page for page in queue.processed_urls.values() if page is not None
        }

    async def close(self) -> None:
        self._closed = True
        if self._session is not None:
            await self._session.close()
