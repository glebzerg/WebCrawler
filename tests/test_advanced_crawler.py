import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from aiohttp import web

from crawler import AdvancedCrawler, CrawlProgress, serialize_page
from tests.http_server import make_test_server


class AdvancedCrawlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_from_config_runs_a_polite_crawl_and_saves_readable_json(self) -> None:
        server = make_test_server()
        requests: list[tuple[str, str]] = []
        progress: list[CrawlProgress] = []
        first_attempts = 0

        async def respond(request: web.Request) -> web.Response:
            nonlocal first_attempts
            path = request.path
            requests.append((path, request.headers["User-Agent"]))
            if path == "/robots.txt":
                return web.Response(text="User-agent: *\nDisallow: /docs/private")
            if path == "/sitemap.xml":
                entries = "".join(
                    f"<url><loc>{server.make_url(page)}</loc></url>"
                    for page in ("/ignore", "/docs/first", "/docs/second", "/docs/extra")
                )
                xml = f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'
                return web.Response(text=xml, content_type="application/xml")
            if path == "/docs/first":
                first_attempts += 1
                if first_attempts == 1:
                    return web.Response(status=503)
            return web.Response(text=f"<title>{path}</title>", content_type="text/html")

        server.app.router.add_get("/{path:.*}", respond)
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            output = Path(directory) / "results" / "pages.json"
            async with server:
                config = {
                    "start_urls": [str(server.make_url("/docs/private"))],
                    "sitemap_urls": [str(server.make_url("/sitemap.xml"))],
                    "max_pages": 3,
                    "max_depth": 0,
                    "max_concurrent": 1,
                    "include_patterns": ["*/docs/*"],
                    "same_domain_only": True,
                    "user_agent": "ConfigTestBot/1.0",
                    "max_retries": 1,
                    "storage": {"path": str(output), "format": "json"},
                }
                config_path.write_text(json.dumps(config), encoding="utf-8")

                crawler = AdvancedCrawler.from_config(config_path)
                try:
                    self.assertFalse(output.parent.exists())
                    async with asyncio.timeout(15):
                        pages = await crawler.crawl(on_progress=progress.append)
                finally:
                    await crawler.close()

                expected_urls = [
                    str(server.make_url(path)) for path in ("/docs/first", "/docs/second")
                ]
                self.assertEqual(list(pages), expected_urls)
                saved = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(
            [path for path, _ in requests],
            ["/robots.txt", "/sitemap.xml", "/docs/first", "/docs/first", "/docs/second"],
        )
        self.assertEqual({agent for _, agent in requests}, {"ConfigTestBot/1.0"})
        self.assertEqual(saved, [serialize_page(pages[url]) for url in expected_urls])
        self.assertEqual(progress[-1]["processed"], 2)
        stats = crawler.get_stats()
        self.assertEqual(stats["saved_pages"], 2)
        self.assertEqual(stats["successful_retries"], 1)
        self.assertEqual(stats["blocked_pages"], 1)
