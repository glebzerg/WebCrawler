import asyncio
import socket
import unittest
from unittest.mock import patch

from aiohttp import (
    ClientConnectorError,
    ClientResponse,
    ClientResponseError,
    SocketTimeoutError,
    web,
)

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

    async def test_fetch_url_logs_started_and_final_events(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        async with make_test_server() as server:
            ok_url = str(server.make_url("/ok"))
            missing_url = str(server.make_url("/missing"))
            with self.assertLogs("crawler.core", level="INFO") as captured:
                await crawler.fetch_url(ok_url)
                with self.assertRaises(ClientResponseError):
                    await crawler.fetch_url(missing_url)

        events = [(record.event, record.url, record.levelname) for record in captured.records]
        self.assertEqual(
            events,
            [
                ("fetch_started", ok_url, "INFO"),
                ("fetch_succeeded", ok_url, "INFO"),
                ("fetch_started", missing_url, "INFO"),
                ("fetch_failed", missing_url, "WARNING"),
            ],
        )
        self.assertEqual(captured.records[-1].error_type, "ClientResponseError")

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

    async def test_session_uses_configured_connection_and_read_timeouts(self) -> None:
        crawler = AsyncCrawler(connect_timeout=2.0, read_timeout=3.0)
        self.addAsyncCleanup(crawler.close)

        session = crawler._get_session()

        self.assertEqual(session.timeout.connect, 2.0)
        self.assertEqual(session.timeout.sock_read, 3.0)

    async def test_fetch_url_times_out_on_stalled_body_and_can_fetch_again(self) -> None:
        body_started = asyncio.Event()
        release_body = asyncio.Event()

        async def stalled_body(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b"First part of the body")
            body_started.set()
            await release_body.wait()
            return response

        crawler = AsyncCrawler(connect_timeout=2.0, read_timeout=0.25)
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/stalled-body", stalled_body)

        async with server:
            try:
                with self.assertRaises(SocketTimeoutError):
                    await crawler.fetch_url(str(server.make_url("/stalled-body")))
                self.assertTrue(body_started.is_set())
            finally:
                release_body.set()

            text = await crawler.fetch_url(str(server.make_url("/ok")))

        self.assertEqual(text, "Hello from the test server")

    async def test_fetch_urls_overlaps_requests_and_keeps_successes(self) -> None:
        both_started = asyncio.Event()
        started: list[str] = []

        async def overlapping_page(request: web.Request) -> web.Response:
            name = request.match_info["name"]
            started.append(name)
            if len(started) == 2:
                both_started.set()
            await both_started.wait()
            return web.Response(text=name)

        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/overlap/{name}", overlapping_page)

        async with server:
            first = str(server.make_url("/overlap/first"))
            second = str(server.make_url("/overlap/second"))
            missing = str(server.make_url("/missing"))
            try:
                async with asyncio.timeout(5):
                    pages = await crawler.fetch_urls([first, missing, second, first])
            finally:
                both_started.set()

        self.assertEqual(pages, {first: "first", second: "second"})
        self.assertCountEqual(started, ["first", "second"])
        self.assertEqual(set(crawler.failed_urls), {missing})
        error = crawler.failed_urls[missing]
        self.assertIsInstance(error, ClientResponseError)
        self.assertEqual(error.status, 404)

    async def test_empty_fetch_batch_resets_previous_failures(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        async with make_test_server() as server:
            missing = str(server.make_url("/missing"))
            self.assertEqual(await crawler.fetch_urls([missing]), {})
            self.assertIn(missing, crawler.failed_urls)

        self.assertEqual(await crawler.fetch_urls([]), {})
        self.assertEqual(crawler.failed_urls, {})

    def test_max_concurrent_must_be_positive(self) -> None:
        for limit in (0, -1):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(ValueError, "max_concurrent"):
                    AsyncCrawler(max_concurrent=limit)

    async def test_fetch_urls_limits_active_requests_and_continues_after_500(self) -> None:
        first_page_started = asyncio.Event()
        later_page_started = asyncio.Event()
        active = 0
        peak = 0

        async def limited_page(request: web.Request) -> web.Response:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)

            try:
                name = request.match_info["name"]
                if name == "error":
                    await first_page_started.wait()
                    return web.Response(status=500)

                if name == "0":
                    first_page_started.set()
                    await later_page_started.wait()
                else:
                    later_page_started.set()

                await asyncio.sleep(0.1)
                return web.Response(text=name)
            finally:
                active -= 1

        crawler = AsyncCrawler(max_concurrent=2)
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/limited/{name}", limited_page)

        async with server:
            failed_url = str(server.make_url("/limited/error"))
            expected = {str(server.make_url(f"/limited/{i}")): str(i) for i in range(5)}
            try:
                async with asyncio.timeout(5):
                    pages = await crawler.fetch_urls([failed_url, *expected])
            finally:
                first_page_started.set()
                later_page_started.set()

        self.assertEqual(peak, 2)
        self.assertEqual(pages, expected)
        self.assertEqual(set(crawler.failed_urls), {failed_url})
        error = crawler.failed_urls[failed_url]
        self.assertIsInstance(error, ClientResponseError)
        self.assertEqual(error.status, 500)

    async def test_cancelled_download_closes_connection_and_releases_slot(self) -> None:
        reading_body = asyncio.Event()
        client_disconnected = asyncio.Event()
        release_body = asyncio.Event()

        async def slow_page(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b"First part of the body")
            try:
                await release_body.wait()
            except asyncio.CancelledError:
                client_disconnected.set()
                raise
            return response

        original_text = ClientResponse.text

        async def observe_body_read(response: ClientResponse) -> str:
            reading_body.set()
            return await original_text(response)

        crawler = AsyncCrawler(max_concurrent=1)
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/slow", slow_page)

        async with server:
            try:
                with patch.object(ClientResponse, "text", observe_body_read):
                    task = asyncio.create_task(
                        crawler.fetch_url(str(server.make_url("/slow")))
                    )
                    try:
                        await asyncio.wait_for(reading_body.wait(), timeout=5)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, timeout=5)
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

                await asyncio.wait_for(client_disconnected.wait(), timeout=5)
                text = await asyncio.wait_for(
                    crawler.fetch_url(str(server.make_url("/ok"))), timeout=5
                )
            finally:
                release_body.set()

        self.assertEqual(text, "Hello from the test server")
