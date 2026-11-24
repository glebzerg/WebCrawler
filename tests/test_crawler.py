import unittest

from crawler import AsyncCrawler
from tests.http_server import make_test_server


class AsyncCrawlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_session_is_reused_and_closed(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        session = crawler._get_session()

        self.assertFalse(session.closed)
        self.assertIs(crawler._get_session(), session)

        await crawler.close()
        self.assertTrue(session.closed)

        await crawler.close()
        with self.assertRaisesRegex(RuntimeError, "AsyncCrawler is closed"):
            crawler._get_session()

    async def test_close_before_first_use(self) -> None:
        crawler = AsyncCrawler()

        await crawler.close()

        with self.assertRaisesRegex(RuntimeError, "AsyncCrawler is closed"):
            crawler._get_session()

    async def test_fetch_url_returns_response_text(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        async with make_test_server() as server:
            text = await crawler.fetch_url(str(server.make_url("/ok")))

        self.assertEqual(text, "Hello from the test server")

    async def test_fetch_url_returns_empty_string_for_empty_body(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        async with make_test_server() as server:
            text = await crawler.fetch_url(str(server.make_url("/empty")))

        self.assertEqual(text, "")
