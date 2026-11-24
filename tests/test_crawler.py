import socket
import unittest

from aiohttp import ClientConnectorError, ClientResponseError

from crawler import AsyncCrawler, FetchConnectionError
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

    async def test_fetch_url_raises_http_error_with_status_and_url(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        async with make_test_server() as server:
            for path, status in (("/missing", 404), ("/server-error", 500)):
                with self.subTest(status=status):
                    url = server.make_url(path)
                    with self.assertRaises(ClientResponseError) as raised:
                        await crawler.fetch_url(str(url))

                    self.assertEqual(raised.exception.status, status)
                    self.assertEqual(raised.exception.request_info.real_url, url)

    async def test_fetch_url_preserves_connection_failure_url_and_cause(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        with socket.socket() as reserved_port:
            reserved_port.bind(("127.0.0.1", 0))
            port = reserved_port.getsockname()[1]
            url = f"http://127.0.0.1:{port}/ok"

            with self.assertRaises(FetchConnectionError) as raised:
                await crawler.fetch_url(url)

        self.assertEqual(raised.exception.url, url)
        cause = raised.exception.__cause__
        self.assertIsInstance(cause, ClientConnectorError)
        self.assertIsInstance(cause.os_error, ConnectionRefusedError)
