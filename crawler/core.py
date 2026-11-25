import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from math import isfinite
from time import perf_counter
from urllib.parse import urlsplit

from aiohttp import (
    ClientConnectionError,
    ClientConnectorError,
    ClientError,
    ClientSession,
    ClientTimeout,
    InvalidURL,
)

from .errors import classify_error
from .html_parser import HTMLParser
from .page_record import PageRecord
from .queue import CrawlerQueue, QueueStats
from .rate_limiter import RateLimiter
from .retry import RetryStrategy
from .robots_cache import RobotsCache
from .robots_parser import RobotsParser
from .semaphore_manager import SemaphoreManager
from .sitemap_parser import SitemapParser
from .stats import CrawlerStats, CrawlStats
from .storage import DataStorage, StorageRecoveryError
from .urls import resolve_http_url, url_key

logger = logging.getLogger(__name__)
_FETCH_ERRORS = (ClientError, TimeoutError, UnicodeDecodeError)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class CrawlProgress(QueueStats):
    pages_per_second: float
    max_pages: int
    budget_percent: float
    active_requests: int
    eta_seconds: float | None
    finished: bool


@asynccontextmanager
async def _sample_progress(
    report: Callable[[], None], interval: float | None
) -> AsyncIterator[None]:
    if interval is None:
        yield
        return

    async def sample() -> None:
        while True:
            await asyncio.sleep(interval)
            report()

    async with asyncio.TaskGroup() as tasks:
        sampler = tasks.create_task(sample())
        try:
            yield
        finally:
            sampler.cancel()


@dataclass(frozen=True)
class _FetchedResponse:
    text: str
    url: str
    status_code: int
    content_type: str
    location: str | None


class FetchConnectionError(ClientConnectionError):
    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"Failed to connect to {url}")


class RobotsDisallowedError(ClientError):
    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"Blocked by robots.txt: {url}")


class RedirectError(ClientError):
    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        super().__init__(f"Cannot follow redirect from {url}: {reason}")


class AsyncCrawler:
    def __init__(
        self,
        max_concurrent: int = 10,
        *,
        max_per_host: int | None = None,
        requests_per_second: float | None = None,
        per_domain: bool = True,
        min_delay: float = 0.0,
        jitter: float = 0.0,
        user_agent: str = "WebCrawler/0.1",
        respect_robots: bool = False,
        max_redirects: int = 10,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
        total_timeout: float | None = 5 * 60,
        retry_strategy: RetryStrategy | None = None,
        storage: DataStorage | None = None,
        storage_queue_size: int = 10,
        storage_max_retries: int = 2,
    ) -> None:
        if not isinstance(max_redirects, int) or max_redirects < 0:
            raise ValueError("max_redirects must be a non-negative integer")
        if not isinstance(storage_queue_size, int) or storage_queue_size < 1:
            raise ValueError("storage_queue_size must be a positive integer")
        if not isinstance(storage_max_retries, int) or storage_max_retries < 0:
            raise ValueError("storage_max_retries must be a non-negative integer")

        self._max_concurrent = max_concurrent
        self._max_redirects = max_redirects
        self._semaphores = SemaphoreManager(max_concurrent, max_per_host)
        self._rate_limiter = (
            RateLimiter(
                requests_per_second,
                per_domain=per_domain,
                min_delay=min_delay,
                jitter=jitter,
            )
            if requests_per_second is not None or min_delay or jitter or respect_robots
            else None
        )
        self._timeout = ClientTimeout(
            total=total_timeout,
            connect=connect_timeout,
            sock_read=read_timeout,
        )
        self._retry_strategy = retry_strategy
        self._session: ClientSession | None = None
        self._closed = False
        self._user_agent = user_agent
        self._respect_robots = respect_robots
        self._robots_cache = RobotsCache(self._fetch_robots_text)
        self._stats = CrawlerStats()
        self._storage = storage
        self._storage_queue_size = storage_queue_size
        self._storage_max_retries = storage_max_retries
        self._storage_lock = asyncio.Lock()
        self.failed_urls: dict[str, Exception] = {}

    @property
    def active_requests(self) -> int:
        return self._semaphores.active_requests

    def get_stats(self) -> CrawlStats:
        """Return cumulative HTTP and page statistics for this crawler."""
        return self._stats.snapshot()

    def _get_session(self) -> ClientSession:
        if self._closed:
            raise RuntimeError("AsyncCrawler is closed")

        if self._session is None:
            self._session = ClientSession(
                timeout=self._timeout, headers={"User-Agent": self._user_agent}
            )
        return self._session

    async def fetch_url(self, url: str) -> str:
        async with self._track_page(url):
            response = await self._fetch_page(url)
        return response.text

    async def fetch_robots(self, url: str) -> RobotsParser:
        """Load or reuse the robots.txt rules for the URL's site."""
        if self._closed:
            raise RuntimeError("AsyncCrawler is closed")
        with self._stats.measure():
            return await self._robots_cache.get(url)

    async def _fetch_robots_text(self, url: str) -> str:
        response = await self._fetch_page(url, check_robots=False)
        return response.text

    async def fetch_and_parse(
        self, url: str, *, redirect_filter: Callable[[str], bool] | None = None
    ) -> PageRecord:
        async with self._track_page(url):
            response = await self._fetch_page(url, redirect_filter=redirect_filter)
            crawled_at = datetime.now(UTC)
            parsed = await HTMLParser().parse_html(response.text, response.url)
        return {
            **parsed,
            "url": url,
            "status_code": response.status_code,
            "content_type": response.content_type,
            "crawled_at": crawled_at,
        }

    @asynccontextmanager
    async def _track_page(self, url: str) -> AsyncIterator[None]:
        with self._stats.measure():
            try:
                yield
            except _FETCH_ERRORS as error:
                self._stats.record_page_failure(
                    url, error, blocked=isinstance(error, RobotsDisallowedError)
                )
                raise
            else:
                self._stats.record_page_success(url)

    async def _fetch_page(
        self,
        url: str,
        *,
        check_robots: bool = True,
        redirect_filter: Callable[[str], bool] | None = None,
    ) -> _FetchedResponse:
        current_url = url
        seen: set[str] = set()
        redirects = 0

        while True:
            response = await self._fetch_with_retry(current_url, check_robots=check_robots)
            if response.location is None:
                return response

            if redirects >= self._max_redirects:
                raise RedirectError(url, f"redirect limit of {self._max_redirects} reached")
            target = resolve_http_url(response.url, response.location)
            if target is None:
                raise RedirectError(url, f"invalid redirect target: {response.location}")
            if redirect_filter is not None and not redirect_filter(target):
                raise RedirectError(url, f"target outside the crawl scope: {target}")

            seen.add(url_key(response.url))
            if url_key(target) in seen:
                raise RedirectError(url, f"redirect loop at {target}")
            current_url = target
            redirects += 1

    async def _fetch_with_retry(
        self, url: str, *, check_robots: bool
    ) -> _FetchedResponse:
        attempt_number = 0

        async def attempt(timeout: ClientTimeout) -> _FetchedResponse:
            nonlocal attempt_number
            attempt_number += 1
            return await self._fetch_once(
                url, check_robots=check_robots, timeout=timeout, attempt_number=attempt_number
            )

        if self._retry_strategy is None:
            return await attempt(self._timeout)

        def log_retry(error: Exception, delay: float) -> None:
            next_attempt = attempt_number + 1
            logger.info(
                "Retrying %s in %.3f s (attempt %d)",
                url,
                delay,
                next_attempt,
                extra={
                    "event": "fetch_retry_scheduled",
                    "url": url,
                    "attempt": next_attempt,
                    "retry_delay": delay,
                    "error_type": type(error).__name__,
                },
            )

        return await self._retry_strategy.execute_with_timeout(
            attempt, self._timeout, on_retry=log_retry
        )

    async def _fetch_once(
        self, url: str, *, check_robots: bool, timeout: ClientTimeout, attempt_number: int
    ) -> _FetchedResponse:
        crawl_delay = 0.0
        if self._respect_robots and check_robots:
            try:
                rules = await self.fetch_robots(url)
            except ValueError as error:
                raise InvalidURL(url) from error

            if not rules.can_fetch(url, self._user_agent):
                logger.info(
                    "Blocked by robots.txt: %s",
                    url,
                    extra={"event": "robots_blocked", "url": url},
                )
                raise RobotsDisallowedError(url)
            crawl_delay = rules.get_crawl_delay(self._user_agent)

        try:
            host = urlsplit(url).hostname
        except ValueError:
            host = None

        async with self._semaphores.acquire(host):
            session = self._get_session()
            if self._rate_limiter is not None:
                await self._rate_limiter.acquire(host, crawl_delay=crawl_delay)
            logger.info(
                "Started fetching %s",
                url,
                extra={"event": "fetch_started", "url": url, "attempt": attempt_number},
            )
            status_code = None
            self._stats.http_attempts += 1
            started_at = perf_counter()
            try:
                async with session.get(url, allow_redirects=False, timeout=timeout) as response:
                    status_code = response.status
                    response.raise_for_status()
                    location = None
                    if status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("Location") or response.headers.get("URI")
                    text = await response.text() if location is None else ""
                    fetched = _FetchedResponse(
                        text=text,
                        url=str(response.url),
                        status_code=status_code,
                        content_type=response.content_type,
                        location=location,
                    )
            except Exception as error:
                duration = perf_counter() - started_at
                error_type = type(error).__name__
                classified = classify_error(error)
                error_category = type(classified).__name__ if classified is not None else None
                self._stats.record_http_result(
                    attempt_number,
                    duration,
                    status_code=status_code,
                    error_type=error_category or error_type,
                )
                logger.warning(
                    "Failed to fetch %s (%s)",
                    url,
                    error_type,
                    extra={
                        "event": "fetch_failed",
                        "url": url,
                        "attempt": attempt_number,
                        "status_code": status_code,
                        "error_type": error_type,
                        "error_category": error_category,
                    },
                )
                if isinstance(error, ClientConnectorError):
                    raise FetchConnectionError(url) from error
                raise

            self._stats.record_http_result(
                attempt_number, perf_counter() - started_at, status_code=status_code
            )
            logger.info(
                "Finished fetching %s",
                url,
                extra={
                    "event": "fetch_succeeded",
                    "url": url,
                    "attempt": attempt_number,
                    "status_code": status_code,
                },
            )
            return fetched

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
        start_urls: Sequence[str] = (),
        max_pages: int = 100,
        *,
        sitemap_urls: Sequence[str] = (),
        max_depth: int | None = None,
        same_domain_only: bool = False,
        include_patterns: Sequence[str] = (),
        exclude_patterns: Sequence[str] = (),
        on_progress: Callable[[CrawlProgress], None] | None = None,
        progress_interval: float | None = None,
    ) -> dict[str, PageRecord]:
        """Follow eligible links by depth until the page budget is used or the queue is empty."""
        if not isinstance(max_pages, int) or max_pages < 0:
            raise ValueError("max_pages must be a non-negative integer")
        if max_depth is not None and (not isinstance(max_depth, int) or max_depth < 0):
            raise ValueError("max_depth must be a non-negative integer or None")
        if progress_interval is not None and (
            not isfinite(progress_interval) or progress_interval <= 0
        ):
            raise ValueError("progress_interval must be positive and finite or None")

        queue = CrawlerQueue[PageRecord]()
        started_at = perf_counter()

        def report_progress(*, finished: bool = False) -> None:
            if on_progress is None:
                return
            stats = queue.get_stats()
            elapsed = perf_counter() - started_at
            completed = stats["processed"] + stats["failed"]
            rate = completed / elapsed if elapsed > 0 else 0.0
            eta = (max_pages - completed) / rate if rate > 0 and not finished else None
            on_progress(
                {
                    **stats,
                    "pages_per_second": rate,
                    "max_pages": max_pages,
                    "budget_percent": completed / max_pages * 100 if max_pages else 0.0,
                    "active_requests": self.active_requests,
                    "eta_seconds": eta,
                    "finished": finished,
                }
            )

        if max_pages == 0:
            self.failed_urls = {}
            report_progress(finished=True)
            return {}

        allowed_hosts = (
            {urlsplit(url).hostname for url in (*start_urls, *sitemap_urls)} - {None}
            if same_domain_only
            else None
        )

        def host_allowed(url: str) -> bool:
            return allowed_hosts is None or urlsplit(url).hostname in allowed_hosts

        def should_enqueue(url: str) -> bool:
            if not host_allowed(url):
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

        failures: dict[str, Exception] = {}
        remaining_pages = max_pages

        async def crawl_level(
            depth: int, write_queue: asyncio.Queue[PageRecord | None] | None
        ) -> None:
            nonlocal remaining_pages
            while remaining_pages > 0:
                job = await queue.get_next_job(max_depth=depth)
                if job is None:
                    return
                # Dequeuing does not suspend, so the slot is reserved before the next worker runs.
                remaining_pages -= 1
                url, _ = job

                try:
                    page = await self.fetch_and_parse(url, redirect_filter=should_enqueue)
                except _FETCH_ERRORS as error:
                    queue.mark_failed(url, str(error))
                    failures[url] = error
                    report_progress()
                    continue

                if write_queue is not None:
                    await write_queue.put(page)

                queue.mark_processed(url, page)
                if max_depth is None or depth < max_depth:
                    for link in page["links"]:
                        if should_enqueue(link):
                            queue.add_url(link, depth=depth + 1)
                report_progress()

        with self._stats.measure():
            async with (
                _sample_progress(
                    report_progress, progress_interval if on_progress is not None else None
                ),
                self._page_write_queue() as write_queue,
            ):
                async for url in self._sitemap_start_urls(sitemap_urls, host_allowed):
                    if should_enqueue(url):
                        queue.add_url(url)
                if queue.next_depth() is not None:
                    report_progress()

                while remaining_pages > 0 and (depth := queue.next_depth()) is not None:
                    worker_count = min(
                        self._max_concurrent, queue.get_stats()["queued"], remaining_pages
                    )
                    async with asyncio.TaskGroup() as workers:
                        for _ in range(worker_count):
                            workers.create_task(crawl_level(depth, write_queue))

        self.failed_urls = failures
        report_progress(finished=True)
        return {
            page["url"]: page for page in queue.processed_urls.values() if page is not None
        }

    async def _sitemap_start_urls(
        self, sitemap_urls: Sequence[str], host_allowed: Callable[[str], bool]
    ) -> AsyncIterator[str]:
        async def fetch_text(url: str) -> str:
            response = await self._fetch_page(url, redirect_filter=host_allowed)
            return response.text

        def report_error(url: str, error: Exception) -> None:
            logger.warning(
                "Skipping sitemap %s: %s",
                url,
                error,
                extra={"event": "sitemap_failed", "url": url},
            )

        parser = SitemapParser(fetch_text, allow_sitemap=host_allowed)
        async for url in parser.iter_page_urls(sitemap_urls, on_error=report_error):
            yield url

    @asynccontextmanager
    async def _page_write_queue(
        self,
    ) -> AsyncIterator[asyncio.Queue[PageRecord | None] | None]:
        storage = self._storage
        if storage is None:
            yield None
            return

        async with self._storage_lock:
            if self._closed:
                raise RuntimeError("AsyncCrawler is closed")

            queue = asyncio.Queue[PageRecord | None](maxsize=self._storage_queue_size)

            async def write_pages() -> None:
                while (page := await queue.get()) is not None:
                    try:
                        await self._retry_storage(lambda: storage.save(page))
                    except Exception as error:
                        await self._report_storage_failure(storage, error, url=page["url"])
                    else:
                        self._stats.saved_pages += 1

            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(write_pages())
                yield queue
                await queue.put(None)

    async def _retry_storage(self, operation: Callable[[], Awaitable[None]]) -> None:
        for retries in range(self._storage_max_retries + 1):
            try:
                await operation()
                return
            except StorageRecoveryError:
                raise
            except Exception:
                if retries == self._storage_max_retries:
                    raise

    async def _report_storage_failure(
        self, storage: DataStorage, error: Exception, *, url: str | None = None
    ) -> tuple[str, ...]:
        discarded = await storage.discard_failed_batch(error)
        urls = discarded if url is None else (*discarded, url)
        self._stats.record_storage_failure(error, urls, discarded=len(discarded), closing=url is None)
        logger.error(
            "Storage %s failed for %s: %s",
            "close" if url is None else "write",
            ", ".join(urls) or type(storage).__name__,
            error,
            extra={
                "event": "storage_close_failed" if url is None else "storage_write_failed",
                "url": url,
                "affected_urls": urls,
                "error_type": type(error).__name__,
            },
        )
        return discarded

    async def _close_storage(self, storage: DataStorage) -> None:
        try:
            await self._retry_storage(storage.close)
        except Exception as error:
            if not await self._report_storage_failure(storage, error):
                return
            try:
                await storage.close()
            except Exception as cleanup_error:
                await self._report_storage_failure(storage, cleanup_error)
                return
        self._storage = None

    async def close(self) -> None:
        async with self._storage_lock:
            self._closed = True
            try:
                await self._robots_cache.close()
            finally:
                try:
                    if self._session is not None:
                        await self._session.close()
                finally:
                    if self._storage is not None:
                        await self._close_storage(self._storage)
