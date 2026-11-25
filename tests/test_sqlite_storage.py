import json
import sqlite3
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from crawler import PageRecord, SQLiteStorage, serialize_page


def make_page(url: str) -> PageRecord:
    return {
        "url": url,
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


class SQLiteStorageTest(unittest.IsolatedAsyncioTestCase):
    async def test_saving_the_same_url_again_updates_the_persisted_record(self) -> None:
        first = make_page("https://example.test/page")
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

    async def test_full_batches_commit_during_save_and_close_flushes_the_remainder(self) -> None:
        urls = [f"https://example.test/page/{number}" for number in range(1, 6)]

        with TemporaryDirectory() as directory:
            path = Path(directory) / "pages.db"
            storage = SQLiteStorage(path, batch_size=2)
            visible_counts = []
            try:
                await storage.init_db()
                with closing(sqlite3.connect(path)) as reader:
                    for url in urls:
                        await storage.save(make_page(url))
                        rows = reader.execute("SELECT url FROM pages").fetchall()
                        visible_counts.append(len(rows))
            finally:
                await storage.close()
            await storage.close()

            self.assertEqual(visible_counts, [0, 2, 2, 4, 4])
            with closing(sqlite3.connect(path)) as reader:
                rows = reader.execute("SELECT url FROM pages ORDER BY url").fetchall()
            self.assertEqual(rows, [(url,) for url in urls])

    async def test_failed_batch_rolls_back_and_the_triggering_save_can_be_retried(self) -> None:
        first = make_page("https://example.test/first")
        second = make_page("https://example.test/second")

        with TemporaryDirectory() as directory:
            path = Path(directory) / "pages.db"
            storage = SQLiteStorage(path, batch_size=2)
            try:
                await storage.init_db()
                with closing(sqlite3.connect(path)) as reader:
                    reader.execute("""
                        CREATE TRIGGER reject_second BEFORE INSERT ON pages
                        WHEN NEW.url = 'https://example.test/second'
                        BEGIN SELECT RAISE(ABORT, 'test write failure'); END;
                    """)
                    reader.commit()

                    await storage.save(first)
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "test write failure"):
                        await storage.save(second)
                    self.assertEqual(reader.execute("SELECT url FROM pages").fetchall(), [])

                    reader.execute("DROP TRIGGER reject_second")
                    reader.commit()
                    await storage.save(second)
                    rows = reader.execute("SELECT url FROM pages ORDER BY url").fetchall()
                    self.assertEqual(rows, [(first["url"],), (second["url"],)])
            finally:
                await storage.close()
