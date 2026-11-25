import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from aiohttp import web
from bs4 import BeautifulSoup

from crawler import AdvancedCrawler, CrawlProgress, CrawlerConfig, serialize_page
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
            report = Path(directory) / "reports" / "stats.json"
            html_report = report.with_name("report.html")
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
                await crawler.export_to_json(report)
                await crawler.export_to_html_report(html_report)
                with self.assertRaisesRegex(ValueError, "different files"):
                    await crawler.export_to_json(output)
                saved = json.loads(output.read_text(encoding="utf-8"))
                exported_stats = json.loads(report.read_text(encoding="utf-8"))
                html = BeautifulSoup(html_report.read_text(encoding="utf-8"), "lxml")

        self.assertEqual(
            [path for path, _ in requests],
            ["/robots.txt", "/sitemap.xml", "/docs/first", "/docs/first", "/docs/second"],
        )
        self.assertEqual({agent for _, agent in requests}, {"ConfigTestBot/1.0"})
        self.assertEqual(saved, [serialize_page(pages[url]) for url in expected_urls])
        self.assertEqual(progress[-1]["processed"], 2)
        stats = crawler.get_stats()
        self.assertEqual(exported_stats, stats)
        status_rows = html.select("#status-counts tbody tr")
        self.assertEqual(
            {row.th.get_text(): int(row.td.get_text()) for row in status_rows},
            exported_stats["status_counts"],
        )
        self.assertEqual(
            {row.th.get_text(): int(row.meter["value"]) for row in status_rows},
            exported_stats["status_counts"],
        )
        overview = {item.dt.get_text(): item.dd.get_text() for item in html.select(".metrics div")}
        self.assertEqual(int(overview["Processed"]), exported_stats["processed_pages"])
        self.assertEqual(stats["saved_pages"], 2)
        self.assertEqual(stats["successful_retries"], 1)
        self.assertEqual(stats["blocked_pages"], 1)

    async def test_html_report_escapes_urls_and_error_messages(self) -> None:
        crawler = AdvancedCrawler(CrawlerConfig())
        self.addAsyncCleanup(crawler.close)
        stats = crawler.get_stats()
        url = "https://example.test/<entry>?x=1&y=2"
        message = 'Ошибка <example> & "кавычки"'
        stats["permanent_errors"] = {url: message}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            with patch.object(crawler, "get_stats", return_value=stats):
                await crawler.export_to_html_report(path)
            html = BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")

        row = html.select_one("#reported-errors tbody tr")
        self.assertEqual(row.th.get_text(), url)
        self.assertEqual(row.td.get_text(), message)
        self.assertIsNone(row.select_one("entry, example"))
        self.assertIn("No HTTP responses", html.select_one("#status-counts").get_text())

    async def test_failed_report_replacement_preserves_the_existing_file(self) -> None:
        crawler = AdvancedCrawler(CrawlerConfig())
        self.addAsyncCleanup(crawler.close)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "stats.json"
            path.write_text('{"previous": true}', encoding="utf-8")

            with (
                patch("crawler.reporting.aiofiles.os.replace", side_effect=OSError("report failure")),
                self.assertRaisesRegex(OSError, "report failure"),
            ):
                await crawler.export_to_json(path)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"previous": True})
            self.assertEqual(list(path.parent.iterdir()), [path])
