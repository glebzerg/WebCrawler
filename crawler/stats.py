from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import TypedDict
from urllib.parse import urlsplit

from .errors import PermanentError, classify_error


class CrawlStats(TypedDict):
    """Cumulative snapshot with JSON-ready HTTP status keys."""

    http_attempts: int
    successful_requests: int
    failed_requests: int
    status_counts: dict[str, int]
    total_pages: int
    processed_pages: int
    failed_pages: int
    blocked_pages: int
    top_domains: dict[str, int]
    elapsed_seconds: float
    pages_per_second: float
    errors_by_type: dict[str, int]
    successful_retries: int
    average_retry_duration: float
    permanent_errors: dict[str, str]
    saved_pages: int
    storage_errors: dict[str, str]
    storage_close_error: str | None


class CrawlerStats:
    def __init__(self) -> None:
        self.http_attempts = 0
        self.processed_pages = 0
        self.saved_pages = 0
        self._successful_requests = 0
        self._failed_requests = 0
        self._status_counts: Counter[str] = Counter()
        self._failed_pages = 0
        self._blocked_pages = 0
        self._pages_by_domain: Counter[str] = Counter()
        self._active_operations = 0
        self._started_at: float | None = None
        self._elapsed_seconds = 0.0
        self._errors_by_type: Counter[str] = Counter()
        self._successful_retries = 0
        self._completed_retries = 0
        self._retry_duration = 0.0
        self._permanent_errors: dict[str, str] = {}
        self._storage_errors: dict[str, str] = {}
        self._storage_close_error: str | None = None

    def record_http_result(
        self,
        attempt_number: int,
        duration: float,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
    ) -> None:
        if status_code is not None:
            self._status_counts[str(status_code)] += 1
        if error_type is None:
            self._successful_requests += 1
        else:
            self._failed_requests += 1
            self._errors_by_type[error_type] += 1
        if attempt_number > 1:
            self._completed_retries += 1
            self._retry_duration += duration
            if error_type is None:
                self._successful_retries += 1

    def record_page_success(self, url: str) -> None:
        self.processed_pages += 1
        host = urlsplit(url).hostname
        if host is not None:
            self._pages_by_domain[host] += 1

    def record_page_failure(self, url: str, error: Exception, *, blocked: bool) -> None:
        if blocked:
            self._blocked_pages += 1
            return

        self._failed_pages += 1
        if isinstance(classify_error(error), PermanentError):
            self._permanent_errors[url] = str(error)

    @contextmanager
    def measure(self) -> Iterator[None]:
        """Measure active operation time and count overlapping operations only once."""
        if self._active_operations == 0:
            self._started_at = perf_counter()
        self._active_operations += 1
        try:
            yield
        finally:
            self._active_operations -= 1
            if self._active_operations == 0:
                self._elapsed_seconds = self._elapsed_now()
                self._started_at = None

    def _elapsed_now(self) -> float:
        if self._started_at is None:
            return self._elapsed_seconds
        return self._elapsed_seconds + (perf_counter() - self._started_at)

    def snapshot(self) -> CrawlStats:
        average_duration = (
            self._retry_duration / self._completed_retries if self._completed_retries else 0.0
        )
        total_pages = self.processed_pages + self._failed_pages + self._blocked_pages
        elapsed = self._elapsed_now()
        ranked_domains = sorted(self._pages_by_domain.items(), key=lambda item: (-item[1], item[0]))
        return {
            "http_attempts": self.http_attempts,
            "successful_requests": self._successful_requests,
            "failed_requests": self._failed_requests,
            "status_counts": dict(self._status_counts),
            "total_pages": total_pages,
            "processed_pages": self.processed_pages,
            "failed_pages": self._failed_pages,
            "blocked_pages": self._blocked_pages,
            "top_domains": dict(ranked_domains),
            "elapsed_seconds": elapsed,
            "pages_per_second": total_pages / elapsed if elapsed > 0 else 0.0,
            "errors_by_type": dict(self._errors_by_type),
            "successful_retries": self._successful_retries,
            "average_retry_duration": average_duration,
            "permanent_errors": self._permanent_errors.copy(),
            "saved_pages": self.saved_pages,
            "storage_errors": self._storage_errors.copy(),
            "storage_close_error": self._storage_close_error,
        }

    def record_storage_failure(
        self, error: Exception, urls: tuple[str, ...], *, discarded: int, closing: bool
    ) -> None:
        self.saved_pages -= discarded
        message = str(error)
        self._storage_errors.update(dict.fromkeys(urls, message))
        if closing:
            self._storage_close_error = message
