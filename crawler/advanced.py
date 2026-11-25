from collections.abc import Callable
from pathlib import Path
from typing import Self

from .config import CrawlerConfig, StorageConfig, load_config
from .core import AsyncCrawler, CrawlProgress
from .page_record import PageRecord
from .retry import RetryStrategy
from .sqlite_storage import SQLiteStorage
from .stats import CrawlStats
from .storage import CSVStorage, DataStorage, JSONLStorage, JSONStorage


def _build_storage(config: StorageConfig | None) -> DataStorage | None:
    if config is None:
        return None

    match config.format:
        case "json":
            return JSONStorage(config.path)
        case "jsonl":
            return JSONLStorage(config.path)
        case "csv":
            return CSVStorage(config.path, encoding=config.encoding)
        case "sqlite":
            return SQLiteStorage(config.path, batch_size=config.batch_size)
        case _:
            raise ValueError(f"Unsupported storage format: {config.format}")


class AdvancedCrawler:
    """Run the components of an AsyncCrawler with a validated configuration."""

    def __init__(self, config: CrawlerConfig) -> None:
        self._config = config
        self._crawler = AsyncCrawler(
            max_concurrent=config.max_concurrent,
            max_per_host=config.max_per_host,
            requests_per_second=config.rate_limit,
            per_domain=config.per_domain,
            min_delay=config.min_delay,
            jitter=config.jitter,
            respect_robots=config.respect_robots,
            user_agent=config.user_agent,
            max_redirects=config.max_redirects,
            connect_timeout=config.connect_timeout,
            read_timeout=config.read_timeout,
            total_timeout=config.total_timeout,
            retry_strategy=RetryStrategy(max_retries=config.max_retries),
            storage=_build_storage(config.storage),
            storage_queue_size=config.storage_queue_size,
            storage_max_retries=config.storage_max_retries,
        )

    @classmethod
    def from_config(cls, path: str | Path) -> Self:
        """Construct a crawler from a JSON configuration file."""
        return cls(load_config(path))

    async def crawl(
        self, *, on_progress: Callable[[CrawlProgress], None] | None = None
    ) -> dict[str, PageRecord]:
        """Run the configured crawl and return the records it collected."""
        config = self._config
        return await self._crawler.crawl(
            start_urls=config.start_urls,
            sitemap_urls=config.sitemap_urls,
            max_pages=config.max_pages,
            max_depth=config.max_depth,
            same_domain_only=config.same_domain_only,
            include_patterns=config.include_patterns,
            exclude_patterns=config.exclude_patterns,
            on_progress=on_progress,
        )

    def get_stats(self) -> CrawlStats:
        """Return the underlying crawler's statistics."""
        return self._crawler.get_stats()

    async def close(self) -> None:
        """Finish saving and close the crawler's resources."""
        await self._crawler.close()
