from collections import Counter
from typing import TypedDict

from .errors import PermanentError, classify_error


class CrawlStats(TypedDict):
    http_attempts: int
    processed_pages: int
    failed_pages: int
    blocked_pages: int
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
        self._failed_pages = 0
        self._blocked_pages = 0
        self._errors_by_type: Counter[str] = Counter()
        self._successful_retries = 0
        self._completed_retries = 0
        self._retry_duration = 0.0
        self._permanent_errors: dict[str, str] = {}
        self._storage_errors: dict[str, str] = {}
        self._storage_close_error: str | None = None

    def record_http_result(
        self, attempt_number: int, duration: float, *, error_type: str | None = None
    ) -> None:
        if error_type is not None:
            self._errors_by_type[error_type] += 1
        if attempt_number > 1:
            self._completed_retries += 1
            self._retry_duration += duration
            if error_type is None:
                self._successful_retries += 1

    def record_page_failure(self, url: str, error: Exception, *, blocked: bool) -> None:
        if blocked:
            self._blocked_pages += 1
            return

        self._failed_pages += 1
        if isinstance(classify_error(error), PermanentError):
            self._permanent_errors[url] = str(error)

    def snapshot(self) -> CrawlStats:
        average_duration = (
            self._retry_duration / self._completed_retries if self._completed_retries else 0.0
        )
        return {
            "http_attempts": self.http_attempts,
            "processed_pages": self.processed_pages,
            "failed_pages": self._failed_pages,
            "blocked_pages": self._blocked_pages,
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
