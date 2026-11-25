import json
import logging
import unittest
from contextlib import redirect_stderr
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from crawler import CrawlerConfig, StorageConfig, configure_logging


class LoggingConfigTest(unittest.TestCase):
    def test_rotates_the_log_and_uses_the_same_json_format_for_file_and_console(self) -> None:
        console = StringIO()
        logger = logging.getLogger("crawler.test")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "logs" / "crawler.log"
            config = CrawlerConfig(
                log_file=str(path), log_level="info", log_max_bytes=450, log_backup_count=2
            )
            with redirect_stderr(console), configure_logging(config):
                logger.debug("Excluded by INFO")
                for attempt in range(1, 10):
                    logger.info(
                        "Получен ответ сервера",
                        extra={"event": "fetch_succeeded", "status_code": 200, "attempt": attempt},
                    )
                logger.warning("Last event")

            archives = sorted(path.parent.glob("crawler.log.*"))
            self.assertEqual([archive.name for archive in archives], ["crawler.log.1", "crawler.log.2"])
            retained = [
                json.loads(line)
                for log_path in (path, *archives)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]

        console_events = [json.loads(line) for line in console.getvalue().splitlines()]
        self.assertEqual(len(console_events), 10)
        self.assertEqual({event["level"] for event in console_events}, {"INFO", "WARNING"})
        self.assertEqual(console_events[-1]["message"], "Last event")
        self.assertEqual(console_events[0]["event"], "fetch_succeeded")
        self.assertEqual(console_events[0]["status_code"], 200)
        self.assertEqual(datetime.fromisoformat(console_events[0]["timestamp"]).tzinfo, UTC)
        self.assertTrue(retained)
        for event in retained:
            self.assertIn(event, console_events)

    def test_reconfiguring_restores_handlers_and_does_not_duplicate_records(self) -> None:
        console = StringIO()
        logger = logging.getLogger("crawler")
        previous = (logger.handlers.copy(), logger.level, logger.propagate)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "crawler.log"
            config = CrawlerConfig(log_file=str(path))
            with redirect_stderr(console):
                for message in ("First run", "Second run"):
                    with configure_logging(config):
                        logger.info(message)
                    self.assertEqual((logger.handlers, logger.level, logger.propagate), previous)
            file_events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        console_events = [json.loads(line) for line in console.getvalue().splitlines()]
        self.assertEqual(file_events, console_events)
        self.assertEqual([event["message"] for event in file_events], ["First run", "Second run"])

    def test_rejects_using_page_storage_as_the_log_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pages.json"
            config = CrawlerConfig(log_file=str(path), storage=StorageConfig(str(path)))

            with self.assertRaisesRegex(ValueError, "different files"), configure_logging(config):
                self.fail("Aliased files should be rejected")

            self.assertFalse(path.exists())
