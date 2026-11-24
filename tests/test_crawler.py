import unittest

from crawler import AsyncCrawler


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
