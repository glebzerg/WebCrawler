import json
import sqlite3
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from crawler import PageRecord, SQLiteStorage, serialize_page


class SQLiteStorageTest(unittest.IsolatedAsyncioTestCase):
    async def test_saving_the_same_url_again_updates_the_persisted_record(self) -> None:
        first: PageRecord = {
            "url": "https://example.test/page",
            "title": "Черновик",
            "text": "Первый текст",
            "links": ["https://example.test/old"],
            "images": [],
            "headings": [],
            "lists": [],
            "tables": [],
            "metadata": {"title": "Черновик"},
            "status_code": 200,
            "content_type": "text/html",
            "crawled_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
        updated: PageRecord = {
            **first,
            "title": 'Готово: "страница"',
            "text": "Первая строка\nВторая строка",
            "links": ["https://example.test/new"],
            "metadata": {"title": 'Готово: "страница"'},
            "crawled_at": datetime(2026, 1, 2, tzinfo=UTC),
        }

        with TemporaryDirectory() as directory:
            path = Path(directory) / "results" / "pages.db"
            storage = SQLiteStorage(path)
            try:
                await storage.init_db()
                await storage.init_db()
                await storage.save(first)
            finally:
                await storage.close()

            storage = SQLiteStorage(path)
            try:
                await storage.save(updated)
            finally:
                await storage.close()
            await storage.close()

            with closing(sqlite3.connect(path)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute("SELECT * FROM pages").fetchall()
                columns = connection.execute("PRAGMA table_info(pages)").fetchall()

            self.assertEqual([column["name"] for column in columns if column["pk"]], ["url"])
            self.assertEqual(len(rows), 1)
            saved = dict(rows[0])
            for field in ("links", "images", "headings", "lists", "tables", "metadata"):
                saved[field] = json.loads(saved[field])
            self.assertEqual(saved, serialize_page(updated))
