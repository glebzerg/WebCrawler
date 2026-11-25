import json
import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from queue import SimpleQueue

from .config import CrawlerConfig
from .reporting import same_destination


_EVENT_FIELDS = (
    "url", "attempt", "status_code", "retry_delay", "error_type", "error_category",
    "affected_urls", "field",
)


class _EventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = vars(record)
        event = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": fields.get("event", "message"),
            "message": record.getMessage(),
        }
        event.update({name: fields[name] for name in _EVENT_FIELDS if name in fields})
        return json.dumps(event, ensure_ascii=False)


@contextmanager
def configure_logging(config: CrawlerConfig) -> Iterator[None]:
    """Send crawler events to the console and an optional rotating JSON log."""
    logger = logging.getLogger("crawler")
    old_handlers = logger.handlers.copy()
    old_level = logger.level
    old_propagate = logger.propagate

    with ExitStack() as cleanup:
        console = logging.StreamHandler()
        cleanup.callback(console.close)
        handlers: list[logging.Handler] = [console]
        if config.log_file is not None:
            path = Path(config.log_file)
            if config.storage is not None and same_destination(path, Path(config.storage.path)):
                raise ValueError("log_file and page storage must use different files")
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                path,
                maxBytes=config.log_max_bytes,
                backupCount=config.log_backup_count,
                encoding="utf-8",
            )
            cleanup.callback(file_handler.close)
            handlers.append(file_handler)

        formatter = _EventFormatter()
        for handler in handlers:
            handler.setFormatter(formatter)

        events = SimpleQueue[logging.LogRecord]()
        queued = QueueHandler(events)
        queued.setLevel(config.log_level)
        cleanup.callback(queued.close)
        listener = QueueListener(events, *handlers)
        listener.start()
        cleanup.callback(listener.stop)

        for handler in old_handlers:
            logger.removeHandler(handler)
        logger.addHandler(queued)
        logger.setLevel(config.log_level)
        logger.propagate = False
        try:
            yield
        finally:
            logger.removeHandler(queued)
            for handler in old_handlers:
                logger.addHandler(handler)
            logger.setLevel(old_level)
            logger.propagate = old_propagate
