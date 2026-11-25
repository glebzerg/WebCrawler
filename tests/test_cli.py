import asyncio
import json
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from aiohttp import web

from crawler.cli import main
from tests.http_server import make_test_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CommandLineOptionsTest(unittest.TestCase):
    def test_explicit_flags_override_only_the_corresponding_config_fields(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "start_urls": ["https://example.test/old"],
                        "sitemap_urls": ["https://example.test/sitemap.xml"],
                        "max_pages": 5,
                        "max_depth": 4,
                        "rate_limit": 1,
                        "respect_robots": True,
                        "log_file": None,
                        "storage": {"path": "old.csv", "format": "csv", "encoding": "cp1251"},
                    }
                ),
                encoding="utf-8",
            )

            with patch("crawler.cli._run", new_callable=AsyncMock, return_value=0) as run:
                status = main(
                    [
                        "--config", str(path),
                        "--urls", "https://example.test/new",
                        "--max-depth", "0",
                        "--rate-limit", "2.5",
                        "--no-respect-robots",
                        "--output", "new-output",
                    ]
                )

        self.assertEqual(status, 0)
        run.assert_awaited_once()
        config = run.await_args.args[0]
        self.assertEqual(config.start_urls, ("https://example.test/new",))
        self.assertEqual(config.sitemap_urls, ("https://example.test/sitemap.xml",))
        self.assertEqual(config.max_pages, 5)
        self.assertEqual(config.max_depth, 0)
        self.assertEqual(config.rate_limit, 2.5)
        self.assertFalse(config.respect_robots)
        self.assertEqual(config.storage.path, "new-output")
        self.assertEqual(config.storage.format, "csv")
        self.assertEqual(config.storage.encoding, "cp1251")

    def test_help_lists_the_supported_flags_without_running_the_crawler(self) -> None:
        output = StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            main(["--help"])

        self.assertEqual(stopped.exception.code, 0)
        for flag in ("--urls", "--max-pages", "--max-depth", "--output", "--config",
                     "--respect-robots", "--rate-limit"):
            self.assertIn(flag, output.getvalue())


class CommandLineEntryPointsTest(unittest.IsolatedAsyncioTestCase):
    async def run_cli(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *args,
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
        return stdout.decode("utf-8", errors="replace")

    async def test_module_and_script_crawl_with_config_and_overridden_budget(self) -> None:
        requested: list[str] = []

        async def page(request: web.Request) -> web.Response:
            requested.append(request.path)
            return web.Response(text=f"<title>{request.path}</title>", content_type="text/html")

        server = make_test_server()
        server.app.router.add_get("/{path:.*}", page)
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            configured_output = Path(directory) / "configured.json"
            override_output = Path(directory) / "override.json"
            log_path = Path(directory) / "crawler.log"
            async with server:
                paths = [f"/pages/{number}" for number in range(5)]
                urls = [str(server.make_url(path)) for path in paths]
                config_path.write_text(
                    json.dumps(
                        {
                            "start_urls": urls,
                            "max_concurrent": 1,
                            "max_pages": 5,
                            "respect_robots": False,
                            "log_file": str(log_path),
                            "storage": {"path": str(configured_output)},
                        }
                    ),
                    encoding="utf-8",
                )

                from_config = await self.run_cli("-m", "crawler", "--config", str(config_path))
                with_override = await self.run_cli(
                    "crawler.py", "--config", str(config_path),
                    "--max-pages", "2", "--output", str(override_output),
                )

            saved_from_config = json.loads(configured_output.read_text(encoding="utf-8"))
            saved_with_override = json.loads(override_output.read_text(encoding="utf-8"))
            events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

        self.assertIn("Processed 5 pages", from_config)
        self.assertIn("Processed 2 pages", with_override)
        self.assertEqual([record["url"] for record in saved_from_config], urls)
        self.assertEqual([record["url"] for record in saved_with_override], urls[:2])
        self.assertEqual(requested, [*paths, *paths[:2]])
        self.assertEqual(sum(event["event"] == "fetch_started" for event in events), 7)
        self.assertEqual(sum(event["event"] == "fetch_succeeded" for event in events), 7)
