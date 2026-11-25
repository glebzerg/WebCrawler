import json
from dataclasses import dataclass, fields
from math import isfinite
from pathlib import Path
from typing import Any

from .urls import resolve_http_url


def _check_int(name: str, value: object, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _check_number(name: str, value: object, *, positive: bool = False) -> float:
    limit = "positive" if positive else "non-negative"
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be {limit} and finite")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be {limit} and finite") from error
    if not isfinite(number) or number < 0 or (positive and number == 0):
        raise ValueError(f"{name} must be {limit} and finite")
    return number


def _read_strings(name: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{name} must be an array of non-empty strings")
    return tuple(value)


@dataclass(frozen=True)
class StorageConfig:
    path: str
    format: str = "json"
    encoding: str = "utf-8"
    batch_size: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip() or "\0" in self.path:
            raise ValueError("storage.path must be a non-empty file path")
        if self.format not in ("json", "jsonl", "csv", "sqlite"):
            raise ValueError("storage.format must be json, jsonl, csv, or sqlite")
        if not isinstance(self.encoding, str) or not self.encoding:
            raise ValueError("storage.encoding must be a text encoding")
        try:
            "".encode(self.encoding)
        except LookupError as error:
            raise ValueError(f"storage.encoding is not a text encoding: {self.encoding}") from error
        _check_int("storage.batch_size", self.batch_size, 1)
        if self.format != "csv" and self.encoding != "utf-8":
            raise ValueError("storage.encoding can be changed only for CSV")
        if self.format != "sqlite" and self.batch_size != 1:
            raise ValueError("storage.batch_size can be changed only for SQLite")


@dataclass(frozen=True)
class CrawlerConfig:
    start_urls: tuple[str, ...] = ()
    sitemap_urls: tuple[str, ...] = ()
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    max_pages: int = 100
    max_depth: int | None = None
    same_domain_only: bool = False
    max_concurrent: int = 10
    max_per_host: int | None = None
    rate_limit: float | None = None
    per_domain: bool = True
    min_delay: float = 0.0
    jitter: float = 0.0
    respect_robots: bool = True
    user_agent: str = "WebCrawler/0.1"
    max_redirects: int = 10
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    total_timeout: float | None = 5 * 60
    max_retries: int = 3
    storage_queue_size: int = 10
    storage_max_retries: int = 2
    storage: StorageConfig | None = None

    def __post_init__(self) -> None:
        for name in ("start_urls", "sitemap_urls"):
            urls = []
            for index, source in enumerate(_read_strings(name, getattr(self, name))):
                url = resolve_http_url("", source)
                if url is None or any(char.isspace() for char in url):
                    raise ValueError(f"{name}[{index}] must be an absolute HTTP(S) URL")
                urls.append(url)
            object.__setattr__(self, name, tuple(urls))

        for name in ("include_patterns", "exclude_patterns"):
            object.__setattr__(self, name, _read_strings(name, getattr(self, name)))

        for name in ("max_pages", "max_redirects", "max_retries", "storage_max_retries"):
            _check_int(name, getattr(self, name), 0)
        for name in ("max_concurrent", "storage_queue_size"):
            _check_int(name, getattr(self, name), 1)
        if self.max_depth is not None:
            _check_int("max_depth", self.max_depth, 0)
        if self.max_per_host is not None:
            _check_int("max_per_host", self.max_per_host, 1)

        for name in ("min_delay", "jitter"):
            _check_number(name, getattr(self, name))
        for name in ("connect_timeout", "read_timeout"):
            _check_number(name, getattr(self, name), positive=True)
        if self.total_timeout is not None:
            _check_number("total_timeout", self.total_timeout, positive=True)
        if self.rate_limit is not None:
            rate = _check_number("rate_limit", self.rate_limit, positive=True)
            if not isfinite(1.0 / rate):
                raise ValueError("rate_limit is too small for a finite interval")

        for name in ("respect_robots", "same_domain_only", "per_domain"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be true or false")
        if not isinstance(self.user_agent, str) or not self.user_agent.strip() or any(
            ord(char) < 32 or ord(char) == 127 for char in self.user_agent
        ):
            raise ValueError("user_agent must be a non-empty HTTP header value")
        if self.storage is not None and not isinstance(self.storage, StorageConfig):
            raise ValueError("storage must be a StorageConfig or None")


def _read_options(data: object, config_type: type, name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be a JSON object")
    unknown = data.keys() - {field.name for field in fields(config_type)}
    if unknown:
        raise ValueError(f"{name} has unknown fields: {', '.join(sorted(unknown))}")
    return data.copy()


def load_config(path: str | Path) -> CrawlerConfig:
    """Read and validate a JSON configuration before constructing a crawler."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}:{error.lineno}:{error.colno}: {error.msg}") from error

    options = _read_options(data, CrawlerConfig, "configuration")
    if options.get("storage") is not None:
        storage = _read_options(options["storage"], StorageConfig, "storage")
        if "path" not in storage:
            raise ValueError("storage.path is required")
        options["storage"] = StorageConfig(**storage)
    return CrawlerConfig(**options)
