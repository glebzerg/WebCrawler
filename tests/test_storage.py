import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import aiofiles
from aiohttp import web

from crawler import AsyncCrawler, JSONLStorage, JSONStorage, serialize_page
from tests.http_server import make_test_server


class JSONStorageTest(unittest.IsolatedAsyncioTestCase):
    async def test_crawl_writes_formatted_utf8_json_with_zero_one_or_two_records(self) -> None:
        async def local_page(request: web.Request) -> web.Response:
            number = request.match_info["number"]
            return web.Response(
                text=f"<title>Страница {number}</title><p>Русский текст</p>",
                content_type="text/html",
            )

        server = make_test_server()
        server.app.router.add_get("/pages/{number}", local_page)

        with TemporaryDirectory() as directory:
            async with server:
                for count in (0, 1, 2):
                    with self.subTest(count=count):
                        path = Path(directory) / "results" / f"pages-{count}.json"
                        storage = JSONStorage(path)
                        crawler = AsyncCrawler(max_concurrent=1, storage=storage)
                        urls = [
                            str(server.make_url(f"/pages/{number}"))
                            for number in range(1, count + 1)
                        ]
                        try:
                            pages = await crawler.crawl(urls)
                        finally:
                            await crawler.close()
                        await storage.close()

                        content = path.read_text(encoding="utf-8")
                        saved = json.loads(content)
                        self.assertEqual(len(saved), count)
                        self.assertEqual(saved, [serialize_page(pages[url]) for url in urls])
                        if count:
                            self.assertIn('\n    "title": "Страница 1"', content)
                            self.assertIn("Русский текст", content)
                            with self.assertRaisesRegex(RuntimeError, "JSONStorage is closed"):
                                await storage.save(pages[urls[0]])
                        else:
                            self.assertEqual(content, "[]\n")


class JSONLStorageTest(unittest.IsolatedAsyncioTestCase):
    async def test_crawl_writes_three_independent_lines_and_preserves_newlines(self) -> None:
        multiline_text = "Первая строка\nВторая строка"

        async def local_page(request: web.Request) -> web.Response:
            number = request.match_info["number"]
            text = multiline_text if number == "2" else f"Страница {number}"
            return web.Response(text=f"<pre>{text}</pre>", content_type="text/html")

        server = make_test_server()
        server.app.router.add_get("/pages/{number}", local_page)

        with TemporaryDirectory() as directory:
            path = Path(directory) / "results" / "pages.jsonl"
            storage = JSONLStorage(path)
            crawler = AsyncCrawler(max_concurrent=1, storage=storage)
            async with server:
                urls = [str(server.make_url(f"/pages/{number}")) for number in range(1, 4)]
                try:
                    pages = await crawler.crawl(urls)
                finally:
                    await crawler.close()
            await storage.close()

            saved = []
            async with aiofiles.open(path, encoding="utf-8") as file:
                async for line in file:
                    self.assertTrue(line.endswith("\n"))
                    if len(saved) == 1:
                        self.assertIn(r"Первая строка\nВторая строка", line)
                    saved.append(json.loads(line))

            self.assertEqual(len(saved), 3)
            self.assertEqual(saved, [serialize_page(pages[url]) for url in urls])
            self.assertEqual(saved[1]["text"], multiline_text)
            with self.assertRaisesRegex(RuntimeError, "JSONLStorage is closed"):
                await storage.save(pages[urls[0]])

    async def test_closing_without_records_creates_an_empty_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "empty.jsonl"
            storage = JSONLStorage(path)

            await storage.close()

            self.assertEqual(path.read_bytes(), b"")
