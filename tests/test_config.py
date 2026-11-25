import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from crawler import CrawlerConfig, StorageConfig, load_config


class CrawlerConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(TemporaryDirectory())
        self.path = Path(directory) / "config.json"

    def load(self, data: object) -> CrawlerConfig:
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return load_config(self.path)

    def test_minimal_config_receives_defaults(self) -> None:
        config = self.load({"start_urls": ["https://example.test/"]})

        self.assertEqual(config.start_urls, ("https://example.test/",))
        self.assertEqual(config.sitemap_urls, ())
        self.assertEqual(config.max_pages, 100)
        self.assertEqual(config.max_concurrent, 10)
        self.assertIsNone(config.max_depth)
        self.assertIsNone(config.rate_limit)
        self.assertTrue(config.respect_robots)
        self.assertIsNone(config.storage)

    def test_reads_sitemap_filters_and_storage_options_without_creating_the_output(self) -> None:
        output = self.path.parent / "results" / "pages.db"
        config = self.load(
            {
                "sitemap_urls": ["https://example.test/sitemap.xml"],
                "include_patterns": ["*/docs/*"],
                "exclude_patterns": ["*/draft/*"],
                "max_depth": 0,
                "rate_limit": 2.5,
                "storage": {"path": str(output), "format": "sqlite", "batch_size": 5},
            }
        )

        self.assertEqual(config.sitemap_urls, ("https://example.test/sitemap.xml",))
        self.assertEqual(config.include_patterns, ("*/docs/*",))
        self.assertEqual(config.exclude_patterns, ("*/draft/*",))
        self.assertEqual(config.max_depth, 0)
        self.assertEqual(config.rate_limit, 2.5)
        self.assertEqual(config.storage, StorageConfig(str(output), "sqlite", batch_size=5))
        self.assertFalse(output.parent.exists())

    def test_rejects_invalid_settings_with_the_field_name(self) -> None:
        for options, name in (
            ({"rate_limit": 0}, "rate_limit"),
            ({"rate_limit": float("inf")}, "rate_limit"),
            ({"max_concurrent": True}, "max_concurrent"),
            ({"sitemap_urls": ["/sitemap.xml"]}, "sitemap_urls"),
            ({"exclude_patterns": "*/old/*"}, "exclude_patterns"),
            ({"respect_robots": "false"}, "respect_robots"),
            ({"log_level": "verbose"}, "log_level"),
            ({"log_max_bytes": 0}, "log_max_bytes"),
            ({"log_backup_count": 0}, "log_backup_count"),
            ({"storage": {"format": "json"}}, "storage.path"),
            ({"storage": {"path": "pages.db", "format": "sqlite", "batch_size": 0}}, "storage.batch_size"),
            ({"storage": {"path": "pages.csv", "format": "csv", "encoding": "unknown"}}, "storage.encoding"),
            ({"max_page": 10}, "max_page"),
        ):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, name):
                self.load(options)

    def test_reports_invalid_json_with_the_file_name_and_location(self) -> None:
        self.path.write_text('{\n  "max_pages":', encoding="utf-8")

        with self.assertRaisesRegex(ValueError, r"config\.json:2:\d+"):
            load_config(self.path)
