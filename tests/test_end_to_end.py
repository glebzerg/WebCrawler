import asyncio
import json
import sqlite3
import sys
import unittest
from collections import Counter
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from aiohttp import web
from bs4 import BeautifulSoup

from crawler import AdvancedCrawler, CrawlerConfig, PageRecord, SQLiteStorage, StorageConfig
from tests.http_server import make_test_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


class EndToEndCrawlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_api_and_cli_match_the_server_journal_database_and_reports(self) -> None:
        server = make_test_server()
        journal: list[tuple[str, int]] = []
        story_attempts = 0

        async def respond(request: web.Request) -> web.Response:
            nonlocal story_attempts
            path = request.path
            match path:
                case "/robots.txt":
                    response = web.Response(text="User-agent: *\nDisallow: /private")
                case "/sitemap.xml":
                    response = web.Response(
                        text=f'<sitemapindex xmlns="{SITEMAP_NS}"><sitemap>'
                        f"<loc>{server.make_url('/pages.xml')}</loc>"
                        "</sitemap></sitemapindex>",
                        content_type="application/xml",
                    )
                case "/pages.xml":
                    entries = "".join(
                        f"<url><loc>{server.make_url(page)}</loc></url>"
                        for page in ("/home", "/home#top", "/private")
                    )
                    response = web.Response(
                        text=f'<urlset xmlns="{SITEMAP_NS}">{entries}</urlset>',
                        content_type="application/xml",
                    )
                case "/home":
                    response = web.Response(
                        text='<title>Home</title><body><h1>Home<a href="/story">Story</a>'
                        '<a href="/jump">Jump</a><a href="/private">Private</a>',
                        content_type="text/html",
                    )
                case "/story":
                    story_attempts += 1
                    if story_attempts == 1:
                        response = web.Response(status=503)
                    else:
                        response = web.Response(
                            text='<title>Story</title><a href="/home">Home</a>',
                            content_type="text/html",
                        )
                case "/jump":
                    response = web.Response(status=302, headers={"Location": "/section/target"})
                case "/section/target":
                    response = web.Response(
                        text='<title>Target</title><a href="next">Next</a>', content_type="text/html"
                    )
                case "/section/next":
                    response = web.Response(text="<title>Next</title>", content_type="text/html")
                case _:
                    response = web.Response(status=404)
            journal.append((path, response.status))
            return response

        server.app.router.add_get("/{path:.*}", respond)
        with TemporaryDirectory() as directory:
            output = Path(directory)
            async with server:
                config = {
                    "sitemap_urls": [str(server.make_url("/sitemap.xml"))],
                    "max_concurrent": 1,
                    "max_pages": 10,
                    "same_domain_only": True,
                    "max_retries": 1,
                    "log_file": None,
                }
                api_path = self.write_config(output, "api", config)
                cli_path = self.write_config(output, "cli", config)
                crawler = AdvancedCrawler.from_config(api_path)
                try:
                    async with asyncio.timeout(15):
                        pages = await crawler.crawl()
                finally:
                    await crawler.close()
                await crawler.export_to_json(output / "api-stats.json")
                await crawler.export_to_html_report(output / "api-report.html")
                api_journal = journal.copy()

                journal.clear()
                story_attempts = 0
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "crawler", "--config", str(cli_path),
                    "--stats-json", str(output / "cli-stats.json"),
                    "--report-html", str(output / "cli-report.html"),
                    cwd=PROJECT_ROOT,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    async with asyncio.timeout(20):
                        stdout, stderr = await process.communicate()
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                self.assertEqual(process.returncode, 0, stderr.decode("utf-8", errors="replace"))
                self.assertIn(b"Finished 5/10 pages", stdout)

                home, story, jump, private, next_page = (
                    str(server.make_url(path))
                    for path in ("/home", "/story", "/jump", "/private", "/section/next")
                )
                expected_pages = {
                    home: ("Home", [story, jump, private]),
                    story: ("Story", [home]),
                    jump: ("Target", [next_page]),
                    next_page: ("Next", []),
                }

            self.assertEqual(
                api_journal,
                [
                    ("/robots.txt", 200), ("/sitemap.xml", 200), ("/pages.xml", 200),
                    ("/home", 200), ("/story", 503), ("/story", 200), ("/jump", 302),
                    ("/section/target", 200), ("/section/next", 200),
                ],
            )
            self.assertEqual(journal, api_journal)
            self.assertEqual(
                {url: (record["title"], record["links"]) for url, record in pages.items()},
                expected_pages,
            )

            status_counts = dict(Counter(str(status) for _, status in journal))
            for entry in ("api", "cli"):
                with self.subTest(entry=entry):
                    with closing(sqlite3.connect(output / f"{entry}.db")) as database:
                        rows = database.execute("SELECT url, title, links FROM pages").fetchall()
                    self.assertEqual(
                        {url: (title, json.loads(links)) for url, title, links in rows},
                        expected_pages,
                    )
                    stats = json.loads((output / f"{entry}-stats.json").read_text(encoding="utf-8"))
                    if entry == "api":
                        self.assertEqual(stats, crawler.get_stats())
                    self.assertEqual(stats["status_counts"], status_counts)
                    self.assertEqual(stats["http_attempts"], len(journal))
                    self.assertEqual(stats["successful_retries"], 1)
                    self.assertEqual(stats["blocked_pages"], 1)
                    self.assertEqual(stats["saved_pages"], len(rows))
                    self.assertEqual(stats["storage_errors"], {})

                    document = (output / f"{entry}-report.html").read_text(encoding="utf-8")
                    html = BeautifulSoup(document, "lxml")
                    self.assertEqual(
                        {
                            row.th.get_text(): int(row.meter["value"])
                            for row in html.select("#status-counts tbody tr")
                        },
                        status_counts,
                    )
                    overview = {
                        item.dt.get_text(): int(item.dd.get_text())
                        for item in html.select(".metrics div") if item.dt.get_text() in {"Saved", "Blocked"}
                    }
                    self.assertEqual(overview, {"Saved": len(rows), "Blocked": 1})

    def write_config(self, output: Path, name: str, config: dict[str, object]) -> Path:
        path = output / f"{name}.json"
        options = {
            **config,
            "storage": {"format": "sqlite", "path": str(output / f"{name}.db"), "batch_size": 3},
        }
        path.write_text(json.dumps(options), encoding="utf-8")
        return path

    async def test_cancellation_closes_session_writer_and_sqlite_with_pending_records(self) -> None:
        stalled = asyncio.Event()
        release = asyncio.Event()
        saved = asyncio.Event()

        class TrackedSQLiteStorage(SQLiteStorage):
            writer: asyncio.Task | None = None

            async def save(self, data: PageRecord) -> None:
                self.writer = asyncio.current_task()
                await super().save(data)
                saved.set()

        async def stall(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b"<p>Partial")
            stalled.set()
            await release.wait()
            return response

        server = make_test_server()
        server.app.router.add_get("/stalled", stall)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cancel.db"
            storage = TrackedSQLiteStorage(path, batch_size=2)
            async with server:
                ready = str(server.make_url("/ok"))
                config = CrawlerConfig(
                    start_urls=(ready, str(server.make_url("/stalled"))),
                    max_concurrent=2,
                    respect_robots=False,
                    storage=StorageConfig(str(path), format="sqlite", batch_size=2),
                )
                with patch("crawler.advanced._build_storage", return_value=storage):
                    crawler = AdvancedCrawler(config)

                async def run() -> None:
                    try:
                        await crawler.crawl()
                    finally:
                        await crawler.close()

                job = asyncio.create_task(run())
                try:
                    async with asyncio.timeout(15):
                        await saved.wait()
                        await stalled.wait()
                        session = crawler._crawler._session
                        connection = storage._connection
                        writer = storage.writer
                        self.assertIsNotNone(session)
                        self.assertIsNotNone(connection)
                        self.assertIsNotNone(writer)
                        self.assertFalse(job.done())
                        self.assertFalse(writer.done())
                        job.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await job
                finally:
                    release.set()
                    if not job.done():
                        job.cancel()
                        await asyncio.gather(job, return_exceptions=True)

            self.assertTrue(session.closed)
            self.assertTrue(writer.cancelled())
            self.assertIsNone(storage._connection)
            with self.assertRaises(ValueError):
                await connection.execute("SELECT 1")
            with closing(sqlite3.connect(path)) as database:
                self.assertEqual(database.execute("SELECT url FROM pages").fetchall(), [(ready,)])
