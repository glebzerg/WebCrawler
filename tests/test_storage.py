import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from aiohttp import web

from crawler import AsyncCrawler, JSONStorage, serialize_page
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
