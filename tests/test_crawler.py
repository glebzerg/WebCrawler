import asyncio
import json
import socket
import threading
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, call, patch

from aiohttp import (
    ClientConnectorError,
    ClientResponse,
    ClientResponseError,
    ClientTimeout,
    SocketTimeoutError,
    web,
)

from crawler import (
    AsyncCrawler,
    CrawlProgress,
    DataStorage,
    FetchConnectionError,
    HTMLParser,
    PageRecord,
    RateLimiter,
    RedirectError,
    RetryStrategy,
    RobotsDisallowedError,
    RobotsUnavailableError,
    serialize_page,
)
from crawler.html_parser import ParsedPage
from tests.http_server import make_test_server


class MemoryStorage(DataStorage):
    def __init__(self) -> None:
        self.records: list[PageRecord] = []
        self.saved_counts_at_close: list[int] = []

    async def save(self, data: PageRecord) -> None:
        self.records.append(data)

    async def close(self) -> None:
        self.saved_counts_at_close.append(len(self.records))


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
        self.assertEqual(captured.records[-1].error_category, "PermanentError")

    async def test_fetch_url_returns_empty_string_for_empty_body(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        async with make_test_server() as server:
            text = await crawler.fetch_url(str(server.make_url("/empty")))

        self.assertEqual(text, "")

    async def test_fetch_url_sends_configured_user_agent(self) -> None:
        async def echo_agent(request: web.Request) -> web.Response:
            return web.Response(text=request.headers["User-Agent"])

        crawler = AsyncCrawler(user_agent="ScoutBot/1.0")
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/agent", echo_agent)

        async with server:
            text = await crawler.fetch_url(str(server.make_url("/agent")))

        self.assertEqual(text, "ScoutBot/1.0")

    async def test_fetch_robots_shares_one_request_per_site_and_separates_ports(self) -> None:
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        requests: list[tuple[str, str]] = []

        async def first_robots(request: web.Request) -> web.Response:
            requests.append(("first", request.headers["User-Agent"]))
            first_started.set()
            await release_first.wait()
            return web.Response(text="User-agent: ScoutBot\nDisallow: /first-private")

        async def second_robots(request: web.Request) -> web.Response:
            requests.append(("second", request.headers["User-Agent"]))
            return web.Response(text="User-agent: ScoutBot\nDisallow: /second-private")

        crawler = AsyncCrawler(user_agent="ScoutBot/1.0")
        self.addAsyncCleanup(crawler.close)
        first_server = make_test_server()
        second_server = make_test_server()
        first_server.app.router.add_get("/robots.txt", first_robots)
        second_server.app.router.add_get("/robots.txt", second_robots)

        async with first_server, second_server:
            urls = [str(first_server.make_url(f"/page/{number}")) for number in range(5)]
            first_loads = asyncio.gather(*(crawler.fetch_robots(url) for url in urls))
            try:
                await asyncio.wait_for(first_started.wait(), timeout=15)
                second = await asyncio.wait_for(
                    crawler.fetch_robots(str(second_server.make_url("/page/1"))), timeout=15
                )
            finally:
                release_first.set()
                first_rules = await asyncio.wait_for(first_loads, timeout=15)
            cached = await crawler.fetch_robots(str(first_server.make_url("/another-page")))

        self.assertTrue(all(rules is cached for rules in first_rules))
        self.assertIsNot(cached, second)
        self.assertFalse(cached.can_fetch("/first-private", "ScoutBot"))
        self.assertTrue(second.can_fetch("/first-private", "ScoutBot"))
        self.assertFalse(second.can_fetch("/second-private", "ScoutBot"))
        self.assertEqual(requests, [("first", "ScoutBot/1.0"), ("second", "ScoutBot/1.0")])

    async def test_missing_robots_file_is_cached_as_empty_rules(self) -> None:
        requests = 0

        async def missing_robots(_request: web.Request) -> web.Response:
            nonlocal requests
            requests += 1
            raise web.HTTPNotFound()

        crawler = AsyncCrawler(respect_robots=True)
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/robots.txt", missing_robots)

        async with server:
            page_url = str(server.make_url("/ok"))
            self.assertEqual(await crawler.fetch_url(page_url), "Hello from the test server")
            rules = await crawler.fetch_robots(page_url)
            self.assertTrue(rules.can_fetch(page_url))
            self.assertIs(rules, await crawler.fetch_robots(page_url))

        self.assertEqual(requests, 1)

    async def test_unavailable_robots_prevents_a_page_request(self) -> None:
        requests = []

        async def unavailable_robots(request: web.Request) -> web.Response:
            requests.append(request.path)
            raise web.HTTPServiceUnavailable()

        async def page(request: web.Request) -> web.Response:
            requests.append(request.path)
            return web.Response(text="Page")

        crawler = AsyncCrawler(respect_robots=True)
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/robots.txt", unavailable_robots)
        server.app.router.add_get("/checked", page)

        async with server:
            page_url = str(server.make_url("/checked"))
            robots_url = str(server.make_url("/robots.txt"))
            with self.assertRaisesRegex(RobotsUnavailableError, "HTTP 503") as raised:
                await crawler.fetch_url(page_url)
            with self.assertRaises(RobotsUnavailableError):
                await crawler.fetch_robots(page_url)

        self.assertEqual(raised.exception.url, robots_url)
        self.assertEqual(raised.exception.status, 503)
        self.assertIsInstance(raised.exception.__cause__, ClientResponseError)
        self.assertEqual(requests, ["/robots.txt"])

    async def test_respect_robots_blocks_paths_and_applies_crawl_delay(self) -> None:
        now = 0.0
        starts: list[tuple[str, float]] = []

        def read_clock() -> float:
            return now

        async def advance_clock(delay: float) -> None:
            nonlocal now
            now += delay

        async def tracked_page(request: web.Request) -> web.Response:
            starts.append((request.path, now))
            if request.path == "/robots.txt":
                return web.Response(
                    text="User-agent: ScoutBot\nDisallow: /private\nCrawl-delay: 1"
                )
            return web.Response(text="Page")

        limiter = RateLimiter(2, min_delay=0.75, clock=read_clock, sleep=advance_clock)
        with patch("crawler.core.RateLimiter", return_value=limiter):
            crawler = AsyncCrawler(
                max_concurrent=1,
                requests_per_second=2,
                min_delay=0.75,
                user_agent="ScoutBot/1.0",
                respect_robots=True,
            )
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/{path:.*}", tracked_page)

        async with server:
            first = str(server.make_url("/public/first"))
            second = str(server.make_url("/public/second"))
            private = str(server.make_url("/private"))
            with self.assertLogs("crawler.core", level="INFO") as captured:
                pages = await asyncio.wait_for(
                    crawler.fetch_urls([private, first, second]), timeout=15
                )

        self.assertEqual(pages, {first: "Page", second: "Page"})
        self.assertEqual(set(crawler.failed_urls), {private})
        self.assertIsInstance(crawler.failed_urls[private], RobotsDisallowedError)
        stats = crawler.get_stats()
        self.assertEqual(stats["http_attempts"], 3)
        self.assertEqual(stats["processed_pages"], 2)
        self.assertEqual(stats["failed_pages"], 0)
        self.assertEqual(stats["blocked_pages"], 1)
        self.assertTrue(
            any(
                record.event == "robots_blocked" and record.url == private
                for record in captured.records
            )
        )
        self.assertEqual(starts[0], ("/robots.txt", 0.0))
        self.assertCountEqual(
            [path for path, _ in starts[1:]], ["/public/first", "/public/second"]
        )
        self.assertEqual([time for _, time in starts], [0.0, 1.0, 2.0])

    async def test_fetch_and_parse_builds_a_page_record_after_redirect(self) -> None:
        async def redirect(_request: web.Request) -> web.Response:
            raise web.HTTPFound("/docs/page.html")

        async def final_page(_request: web.Request) -> web.Response:
            return web.Response(
                text='<title>Docs</title><a href="next.html">Next</a>',
                status=203,
                content_type="application/xhtml+xml",
                charset="utf-8",
            )

        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/start", redirect)
        server.app.router.add_get("/docs/page.html", final_page)
        crawled_at = datetime(2026, 7, 1, 12, 34, 56, tzinfo=UTC)

        async with server:
            original_url = str(server.make_url("/start"))
            expected_link = str(server.make_url("/docs/next.html"))
            with patch("crawler.core.datetime") as clock:
                clock.now.return_value = crawled_at
                page = await crawler.fetch_and_parse(original_url)
                clock.now.assert_called_once_with(UTC)

        self.assertEqual(page["url"], original_url)
        self.assertEqual(page["title"], "Docs")
        self.assertEqual(page["text"], "Next")
        self.assertEqual(page["links"], [expected_link])
        self.assertEqual(page["metadata"]["title"], "Docs")
        self.assertEqual(page["status_code"], 203)
        self.assertEqual(page["content_type"], "application/xhtml+xml")
        self.assertEqual(page["crawled_at"], crawled_at)

        serialized = json.loads(json.dumps(serialize_page(page)))
        self.assertEqual(serialized, {**page, "crawled_at": "2026-07-01T12:34:56+00:00"})

        local_time = datetime.fromisoformat("2026-07-01T15:34:56+03:00")
        self.assertEqual(
            serialize_page({**page, "crawled_at": local_time})["crawled_at"],
            serialized["crawled_at"],
        )

    async def test_redirect_checks_robots_rules_at_the_destination_site(self) -> None:
        requests: list[tuple[str, str]] = []
        destination = make_test_server()
        source = make_test_server()

        async def source_robots(request: web.Request) -> web.Response:
            requests.append(("source", request.path))
            return web.Response(text="User-agent: *\nAllow: /")

        async def start(request: web.Request) -> web.Response:
            requests.append(("source", request.path))
            raise web.HTTPFound(destination.make_url("/private"))

        async def destination_page(request: web.Request) -> web.Response:
            requests.append(("destination", request.path))
            if request.path == "/robots.txt":
                return web.Response(text="User-agent: *\nDisallow: /private")
            return web.Response(text="Private page")

        source.app.router.add_get("/robots.txt", source_robots)
        source.app.router.add_get("/start", start)
        destination.app.router.add_get("/{path:.*}", destination_page)
        crawler = AsyncCrawler(max_concurrent=1, respect_robots=True)
        self.addAsyncCleanup(crawler.close)

        async with source, destination:
            start_url = str(source.make_url("/start"))
            private_url = str(destination.make_url("/private"))
            with self.assertRaises(RobotsDisallowedError) as raised:
                await asyncio.wait_for(crawler.fetch_url(start_url), timeout=15)

        self.assertEqual(raised.exception.url, private_url)
        self.assertEqual(
            requests,
            [
                ("source", "/robots.txt"),
                ("source", "/start"),
                ("destination", "/robots.txt"),
            ],
        )

    async def test_redirect_honors_crawl_host_and_pattern_filters(self) -> None:
        requests = []

        async def scoped_page(request: web.Request) -> web.Response:
            requests.append(request.path)
            if request.path == "/docs/redirect-host":
                raise web.HTTPFound(
                    request.url.with_host("localhost").with_path("/docs/allowed-target")
                )
            if request.path == "/docs/redirect-pattern":
                raise web.HTTPFound("/docs/private/target")
            return web.Response(text="Page")

        server = make_test_server()
        server.app.router.add_get("/{path:.*}", scoped_page)
        crawler = AsyncCrawler(max_concurrent=1)
        self.addAsyncCleanup(crawler.close)

        async with server:
            host_redirect = str(server.make_url("/docs/redirect-host"))
            pattern_redirect = str(server.make_url("/docs/redirect-pattern"))
            pages = await crawler.crawl(
                [host_redirect, pattern_redirect],
                same_domain_only=True,
                include_patterns=["*/docs/*"],
                exclude_patterns=["*/docs/private/*"],
            )

        self.assertEqual(pages, {})
        self.assertEqual(requests, ["/docs/redirect-host", "/docs/redirect-pattern"])
        self.assertEqual(set(crawler.failed_urls), {host_redirect, pattern_redirect})
        for error in crawler.failed_urls.values():
            self.assertIsInstance(error, RedirectError)
            self.assertIn("outside the crawl scope", str(error))

    async def test_redirect_loops_and_limits_stop_before_the_next_request(self) -> None:
        requests: list[str] = []
        locations = {
            "a": "/redirect/b",
            "b": "/redirect/a#top",
            "first": "/redirect/second",
            "second": "/redirect/third",
        }

        async def redirect_page(request: web.Request) -> web.Response:
            name = request.match_info["name"]
            requests.append(name)
            if name in locations:
                raise web.HTTPFound(locations[name])
            return web.Response(text=name)

        server = make_test_server()
        server.app.router.add_get("/redirect/{name}", redirect_page)

        async with server:
            for entry, limit, reason, expected in (
                ("a", 10, "redirect loop", ["a", "b"]),
                ("first", 1, "redirect limit of 1", ["first", "second"]),
            ):
                with self.subTest(entry=entry, limit=limit):
                    requests.clear()
                    crawler = AsyncCrawler(max_redirects=limit)
                    try:
                        url = str(server.make_url(f"/redirect/{entry}"))
                        with self.assertRaisesRegex(RedirectError, reason) as raised:
                            await crawler.fetch_url(url)
                        self.assertEqual(raised.exception.url, url)
                    finally:
                        await crawler.close()
                    self.assertEqual(requests, expected)

    async def test_redirect_reapplies_request_pacing_before_the_next_get(self) -> None:
        now = 0.0
        starts = []

        def read_clock() -> float:
            return now

        async def advance_clock(delay: float) -> None:
            nonlocal now
            now += delay

        async def page(request: web.Request) -> web.Response:
            starts.append((request.path, now))
            if request.path == "/start":
                raise web.HTTPFound("/final")
            return web.Response(text="Final page")

        server = make_test_server()
        server.app.router.add_get("/{path:.*}", page)
        limiter = RateLimiter(2, clock=read_clock, sleep=advance_clock)
        with patch("crawler.core.RateLimiter", return_value=limiter):
            crawler = AsyncCrawler(max_concurrent=1, requests_per_second=2)
        self.addAsyncCleanup(crawler.close)

        async with server:
            body = await crawler.fetch_url(str(server.make_url("/start")))

        self.assertEqual(body, "Final page")
        self.assertEqual(starts, [("/start", 0.0), ("/final", 0.5)])

    async def test_another_request_can_finish_while_html_is_being_parsed(self) -> None:
        test_timeout = 15
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        parsing_started = asyncio.Event()
        resume_parsing = threading.Event()
        original_parse = HTMLParser._parse_html

        def paused_parse(parser: HTMLParser, html: str, url: str) -> ParsedPage:
            if threading.get_ident() == loop_thread:
                raise AssertionError("HTML parsing ran in the event loop thread")
            loop.call_soon_threadsafe(parsing_started.set)
            if not resume_parsing.wait(timeout=test_timeout):
                raise TimeoutError("The parsing test did not release its worker")
            return original_parse(parser, html, url)

        crawler = AsyncCrawler(max_concurrent=1)
        self.addAsyncCleanup(crawler.close)
        async with make_test_server() as server:
            url = str(server.make_url("/ok"))
            with patch.object(HTMLParser, "_parse_html", paused_parse):
                parse_task = asyncio.create_task(crawler.fetch_and_parse(url))
                try:
                    await asyncio.wait_for(parsing_started.wait(), timeout=test_timeout)
                    text = await asyncio.wait_for(crawler.fetch_url(url), timeout=test_timeout)
                    self.assertFalse(parse_task.done())
                finally:
                    resume_parsing.set()
                    page = await asyncio.wait_for(parse_task, timeout=test_timeout)

        self.assertEqual(text, "Hello from the test server")
        self.assertEqual(page["text"], text)

    async def test_crawl_follows_links_and_visits_each_page_in_a_cycle_once(self) -> None:
        requested = []

        async def linked_page(request: web.Request) -> web.Response:
            name = request.match_info["name"]
            requested.append(name)
            next_path = "/pages/b" if name == "a" else "/pages/a#return"
            html = f'<title>Page {name.upper()}</title><a href="{next_path}">Next</a>'
            return web.Response(text=html, content_type="text/html")

        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/pages/{name}", linked_page)

        async with server:
            first = str(server.make_url("/pages/a"))
            second = str(server.make_url("/pages/b"))
            pages = await asyncio.wait_for(crawler.crawl([first]), timeout=5)

        self.assertEqual(requested, ["a", "b"])
        self.assertEqual(list(pages), [first, second])
        self.assertEqual(pages[first]["title"], "Page A")
        self.assertEqual(pages[first]["links"], [second])
        self.assertEqual(pages[second]["title"], "Page B")

    async def test_crawl_returns_empty_for_no_start_urls(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        with patch.object(crawler, "fetch_and_parse") as fetch:
            self.assertEqual(await crawler.crawl([]), {})

        fetch.assert_not_called()

    async def test_crawl_max_depth_zero_fetches_only_start_pages(self) -> None:
        async def start_page(_request: web.Request) -> web.Response:
            return web.Response(text='<a href="/ok">Next page</a>', content_type="text/html")

        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/start", start_page)

        async with server:
            start = str(server.make_url("/start"))
            linked = str(server.make_url("/ok"))
            pages = await crawler.crawl([start], max_depth=0)

        self.assertEqual(list(pages), [start])
        self.assertEqual(pages[start]["links"], [linked])

    async def test_crawl_uses_the_shortest_path_when_applying_max_depth(self) -> None:
        links = {
            "a": ("b", "c"),
            "b": ("d",),
            "c": ("x",),
            "d": ("x",),
            "x": ("y",),
            "y": ("z",),
            "z": (),
        }
        requested = []

        async def linked_page(request: web.Request) -> web.Response:
            name = request.match_info["name"]
            requested.append(name)
            anchors = "".join(
                f'<a href="/graph/{next_page}">{next_page}</a>' for next_page in links[name]
            )
            return web.Response(text=f"<body>{anchors}</body>", content_type="text/html")

        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/graph/{name}", linked_page)

        async with server:
            start = str(server.make_url("/graph/a"))
            next_after_x = str(server.make_url("/graph/y"))
            pages = await asyncio.wait_for(crawler.crawl([start], max_depth=3), timeout=5)

        self.assertEqual(requested[:1], ["a"])
        self.assertCountEqual(requested[1:3], ["b", "c"])
        self.assertCountEqual(requested[3:5], ["d", "x"])
        self.assertEqual(requested[5:], ["y"])
        self.assertIn(next_after_x, pages)

    async def test_crawl_rejects_negative_max_depth(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        with self.assertRaisesRegex(ValueError, "max_depth"):
            await crawler.crawl([], max_depth=-1)

    async def test_crawl_filters_hosts_and_patterns_before_enqueuing(self) -> None:
        requested = []

        async def page_handler(request: web.Request) -> web.Response:
            requested.append((request.url.host, request.path))
            html = "<body>Page</body>"
            if request.path == "/docs/first":
                external = request.url.with_host("127.0.0.2").with_path("/docs/external")
                html = (
                    '<a href="/docs/next">Included</a>'
                    '<a href="/docs/private/notes">Excluded despite include pattern</a>'
                    '<a href="/other">Outside the included section</a>'
                    f'<a href="{external}">Outside the starting hosts</a>'
                )
            return web.Response(text=html, content_type="text/html")

        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/{path:.*}", page_handler)

        async with server:
            first = str(server.make_url("/docs/first"))
            second = str(server.make_url("/docs/second").with_host("localhost"))
            next_page = str(server.make_url("/docs/next"))
            excluded_start = str(server.make_url("/docs/private/start"))

            pages = await crawler.crawl(
                [first, second, excluded_start],
                same_domain_only=True,
                include_patterns=["*/docs/*"],
                exclude_patterns=["*/docs/private/*"],
            )

        self.assertCountEqual(pages, [first, second, next_page])
        self.assertCountEqual(
            requested[:2],
            [("127.0.0.1", "/docs/first"), ("localhost", "/docs/second")],
        )
        self.assertEqual(requested[2:], [("127.0.0.1", "/docs/next")])
        self.assertEqual(crawler.failed_urls, {})

    async def test_crawl_waits_for_active_workers_before_advancing_levels(self) -> None:
        slow_started = asyncio.Event()
        allow_slow_response = asyncio.Event()
        next_level_started = asyncio.Event()
        fast_parsed = asyncio.Event()

        async def page_handler(request: web.Request) -> web.Response:
            name = request.match_info["name"]
            if name == "slow":
                slow_started.set()
                await allow_slow_response.wait()
                text = '<a href="/pages/from-slow">Found by slow page</a>'
            elif name == "fast":
                await slow_started.wait()
                text = '<a href="/pages/from-fast">Found by fast page</a>'
            else:
                next_level_started.set()
                text = name
            return web.Response(text=text, content_type="text/html")

        crawler = AsyncCrawler(max_concurrent=2)
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/pages/{name}", page_handler)
        fetch_and_parse = crawler.fetch_and_parse

        async def observe_parse(url: str, *, redirect_filter=None) -> PageRecord:
            page = await fetch_and_parse(url, redirect_filter=redirect_filter)
            if url.endswith("/fast"):
                fast_parsed.set()
            return page

        async with server:
            slow = str(server.make_url("/pages/slow"))
            fast = str(server.make_url("/pages/fast"))
            from_slow = str(server.make_url("/pages/from-slow"))
            from_fast = str(server.make_url("/pages/from-fast"))
            with patch.object(crawler, "fetch_and_parse", side_effect=observe_parse):
                crawl_task = asyncio.create_task(crawler.crawl([slow, fast]))
                try:
                    await asyncio.wait_for(fast_parsed.wait(), timeout=5)
                    self.assertFalse(crawl_task.done())
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(next_level_started.wait(), timeout=0.1)
                finally:
                    allow_slow_response.set()
                    slow_started.set()
                    pages = await asyncio.wait_for(crawl_task, timeout=5)

        self.assertCountEqual(pages, [slow, fast, from_slow, from_fast])
        self.assertTrue(next_level_started.is_set())

    async def test_crawl_reserves_budget_before_requests_and_counts_failures(self) -> None:
        three_started = asyncio.Event()
        requested = []

        async def page_handler(request: web.Request) -> web.Response:
            name = request.match_info["name"]
            requested.append(name)
            if len(requested) == 3:
                three_started.set()
            await three_started.wait()
            if name == "0":
                return web.Response(status=500)
            return web.Response(text=name)

        crawler = AsyncCrawler(max_concurrent=10)
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/budget/{name}", page_handler)

        async with server:
            urls = [str(server.make_url(f"/budget/{number}")) for number in range(12)]
            try:
                pages = await asyncio.wait_for(crawler.crawl(urls, max_pages=3), timeout=5)
            finally:
                three_started.set()

        self.assertCountEqual(requested, ["0", "1", "2"])
        self.assertCountEqual(pages, urls[1:3])
        self.assertEqual(set(crawler.failed_urls), {urls[0]})
        error = crawler.failed_urls[urls[0]]
        self.assertIsInstance(error, ClientResponseError)
        self.assertEqual(error.status, 500)

    async def test_crawl_uses_one_budget_across_levels(self) -> None:
        async def linked_page(_request: web.Request) -> web.Response:
            return web.Response(text='<a href="/ok">Next page</a>', content_type="text/html")

        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/start", linked_page)

        async with server:
            start = str(server.make_url("/start"))
            pages = await crawler.crawl([start], max_pages=1)

        self.assertEqual(list(pages), [start])

    async def test_crawl_skips_requests_at_zero_budget(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        with patch.object(crawler, "fetch_and_parse") as fetch:
            self.assertEqual(
                await crawler.crawl(["https://example.com/start"], max_pages=0), {}
            )

        fetch.assert_not_called()

    async def test_crawl_reports_progress_after_successes_and_failures(self) -> None:
        async def linked_page(_request: web.Request) -> web.Response:
            html = '<a href="/missing">Missing</a><a href="/ok">Available</a>'
            return web.Response(text=html, content_type="text/html")

        snapshots: list[CrawlProgress] = []
        crawler = AsyncCrawler(max_concurrent=1)
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/start", linked_page)

        async with server:
            start = str(server.make_url("/start"))
            pages = await crawler.crawl([start], on_progress=snapshots.append)

        self.assertEqual(len(snapshots), 4)
        self.assertEqual(
            [
                (snapshot["processed"], snapshot["queued"], snapshot["failed"])
                for snapshot in snapshots
            ],
            [(0, 1, 0), (1, 2, 0), (1, 1, 1), (2, 0, 1)],
        )
        final = snapshots[-1]
        self.assertEqual(final["processed"], len(pages))
        self.assertEqual(final["failed"], len(crawler.failed_urls))
        self.assertEqual(final["visited"], 3)
        self.assertEqual(final["in_progress"], 0)
        self.assertEqual(snapshots[0]["pages_per_second"], 0.0)
        self.assertGreater(final["pages_per_second"], 0.0)

    async def test_crawl_continues_after_http_error(self) -> None:
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        async with make_test_server() as server:
            missing = str(server.make_url("/missing"))
            ok = str(server.make_url("/ok"))
            pages = await crawler.crawl([missing, ok])

        self.assertEqual(list(pages), [ok])
        self.assertEqual(pages[ok]["text"], "Hello from the test server")
        self.assertEqual(set(crawler.failed_urls), {missing})
        error = crawler.failed_urls[missing]
        self.assertIsInstance(error, ClientResponseError)
        self.assertEqual(error.status, 404)

    async def test_crawl_saves_only_successful_pages_and_closes_storage_once(self) -> None:
        storage = MemoryStorage()
        crawler = AsyncCrawler(storage=storage)
        self.addAsyncCleanup(crawler.close)

        async with make_test_server() as server:
            first = str(server.make_url("/ok"))
            missing = str(server.make_url("/missing"))
            second = str(server.make_url("/empty"))
            pages = await crawler.crawl([first, missing, second])

        self.assertEqual(set(pages), {first, second})
        self.assertEqual(set(crawler.failed_urls), {missing})
        self.assertEqual(len(storage.records), 2)
        self.assertEqual({record["url"]: record for record in storage.records}, pages)
        self.assertEqual(storage.saved_counts_at_close, [])

        await crawler.close()
        await crawler.close()

        self.assertEqual(storage.saved_counts_at_close, [2])

    async def test_close_waits_for_a_save_in_progress(self) -> None:
        saving = asyncio.Event()
        finish_save = asyncio.Event()
        closing = asyncio.Event()

        class SlowStorage(MemoryStorage):
            async def save(self, data: PageRecord) -> None:
                saving.set()
                await finish_save.wait()
                await super().save(data)

        storage = SlowStorage()
        crawler = AsyncCrawler(storage=storage)
        self.addAsyncCleanup(crawler.close)
        session = crawler._get_session()

        async def close_crawler() -> None:
            closing.set()
            await crawler.close()

        with patch.object(session, "close", new_callable=AsyncMock) as close_session:
            async with make_test_server() as server, asyncio.TaskGroup() as tasks:
                url = str(server.make_url("/ok"))
                crawl = tasks.create_task(crawler.crawl([url]))
                async with asyncio.timeout(15):
                    await saving.wait()
                    close = tasks.create_task(close_crawler())
                    await closing.wait()
                    close_session.assert_not_awaited()
                    self.assertEqual(storage.saved_counts_at_close, [])
                    self.assertFalse(close.done())
                    finish_save.set()
                    await close
                    pages = await crawl

        self.assertEqual(storage.records, [pages[url]])
        self.assertEqual(storage.saved_counts_at_close, [1])

    async def test_storage_queue_applies_backpressure_and_drains_before_crawl_returns(self) -> None:
        saving = asyncio.Event()
        finish_save = asyncio.Event()
        third_parsed = asyncio.Event()
        requested: list[str] = []
        progress: list[CrawlProgress] = []

        class SlowStorage(MemoryStorage):
            async def save(self, data: PageRecord) -> None:
                saving.set()
                await finish_save.wait()
                await super().save(data)

        async def local_page(request: web.Request) -> web.Response:
            requested.append(request.path)
            return web.Response(text="<p>Page</p>", content_type="text/html")

        storage = SlowStorage()
        crawler = AsyncCrawler(max_concurrent=1, storage=storage, storage_queue_size=1)
        self.addAsyncCleanup(crawler.close)
        fetch_and_parse = crawler.fetch_and_parse

        async def track_parse(url: str, *, redirect_filter=None) -> PageRecord:
            page = await fetch_and_parse(url, redirect_filter=redirect_filter)
            if url.endswith("/pages/3"):
                third_parsed.set()
            return page

        server = make_test_server()
        server.app.router.add_get("/pages/{number}", local_page)
        paths = [f"/pages/{number}" for number in range(1, 5)]
        with patch.object(crawler, "fetch_and_parse", side_effect=track_parse):
            async with server, asyncio.TaskGroup() as tasks:
                urls = [str(server.make_url(path)) for path in paths]
                crawl = tasks.create_task(crawler.crawl(urls, on_progress=progress.append))
                async with asyncio.timeout(15):
                    await saving.wait()
                    await third_parsed.wait()
                    self.assertEqual(requested, paths[:3])
                    self.assertEqual(progress[-1]["processed"], 2)
                    self.assertEqual(storage.records, [])
                    self.assertFalse(crawl.done())

                    finish_save.set()
                    pages = await crawl

        self.assertEqual(requested, paths)
        self.assertEqual(storage.records, [pages[url] for url in urls])
        await crawler.close()
        self.assertEqual(storage.saved_counts_at_close, [4])

    async def test_permanent_storage_failure_is_reported_and_later_pages_are_saved(self) -> None:
        attempts: list[str] = []

        class FailingStorage(MemoryStorage):
            async def save(self, data: PageRecord) -> None:
                attempts.append(data["url"])
                if data["url"].endswith("/empty"):
                    raise OSError("test storage failure")
                await super().save(data)

        storage = FailingStorage()
        crawler = AsyncCrawler(max_concurrent=1, storage=storage, storage_max_retries=1)
        self.addAsyncCleanup(crawler.close)
        async with make_test_server() as server:
            failed = str(server.make_url("/empty"))
            later = str(server.make_url("/ok"))
            with self.assertLogs("crawler.core", level="ERROR") as captured:
                pages = await crawler.crawl([failed, later])

        self.assertEqual(attempts, [failed, failed, later])
        self.assertEqual(set(pages), {failed, later})
        self.assertEqual(crawler.failed_urls, {})
        self.assertEqual(storage.records, [pages[later]])
        self.assertEqual(crawler.get_stats()["saved_pages"], 1)
        self.assertEqual(crawler.get_stats()["storage_errors"], {failed: "test storage failure"})
        self.assertEqual(captured.records[0].event, "storage_write_failed")

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

    async def test_retry_releases_request_slot_and_uses_the_next_timeout(self) -> None:
        waiting_to_retry = asyncio.Event()
        resume_retry = asyncio.Event()
        attempts = 0

        async def flaky_page(_request: web.Request) -> web.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return web.Response(status=503, headers={"Retry-After": "5"})
            return web.Response(text="Recovered")

        async def wait_before_retry(_delay: float) -> None:
            waiting_to_retry.set()
            await resume_retry.wait()

        sleep = AsyncMock(side_effect=wait_before_retry)
        crawler = AsyncCrawler(
            max_concurrent=1,
            max_per_host=1,
            connect_timeout=2,
            read_timeout=3,
            total_timeout=5,
            retry_strategy=RetryStrategy(max_retries=1, sleep=sleep),
        )
        self.addAsyncCleanup(crawler.close)
        session = crawler._get_session()
        server = make_test_server()
        server.app.router.add_get("/flaky", flaky_page)

        with patch.object(session, "get", wraps=session.get) as get:
            async with server, asyncio.TaskGroup() as tasks:
                first = tasks.create_task(crawler.fetch_url(str(server.make_url("/flaky"))))
                async with asyncio.timeout(15):
                    await waiting_to_retry.wait()
                    self.assertEqual(crawler.active_requests, 0)
                    second = await crawler.fetch_url(str(server.make_url("/ok")))
                    self.assertEqual(second, "Hello from the test server")
                    self.assertFalse(first.done())
                    self.assertEqual(attempts, 1)

                    resume_retry.set()
                    self.assertEqual(await first, "Recovered")

        sleep.assert_awaited_once_with(5.0)
        self.assertEqual(attempts, 2)
        self.assertEqual(
            [request.kwargs["timeout"] for request in get.call_args_list],
            [
                ClientTimeout(connect=2, sock_read=3, total=5),
                ClientTimeout(connect=2, sock_read=3, total=5),
                ClientTimeout(connect=4, sock_read=6, total=10),
            ],
        )

    async def test_cancelling_the_retry_wait_stops_the_page_request(self) -> None:
        waiting_to_retry = asyncio.Event()
        attempts = 0

        async def unavailable_page(_request: web.Request) -> web.Response:
            nonlocal attempts
            attempts += 1
            return web.Response(status=503)

        async def wait_before_retry(_delay: float) -> None:
            waiting_to_retry.set()
            await asyncio.Future()

        crawler = AsyncCrawler(
            max_concurrent=1,
            retry_strategy=RetryStrategy(max_retries=1, sleep=wait_before_retry),
        )
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/unavailable", unavailable_page)

        async with server, asyncio.TaskGroup() as tasks:
            fetch = tasks.create_task(crawler.fetch_url(str(server.make_url("/unavailable"))))
            async with asyncio.timeout(15):
                await waiting_to_retry.wait()
                fetch.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await fetch
                self.assertEqual(crawler.active_requests, 0)
                self.assertEqual(
                    await crawler.fetch_url(str(server.make_url("/ok"))),
                    "Hello from the test server",
                )

        self.assertEqual(attempts, 1)

    async def test_retry_rechecks_robots_and_pacing_for_the_failed_redirect_hop(self) -> None:
        now = 0.0
        starts: list[tuple[str, float]] = []
        target_attempts = 0

        def read_clock() -> float:
            return now

        async def advance_clock(delay: float) -> None:
            nonlocal now
            now += delay

        async def respond(request: web.Request) -> web.Response:
            nonlocal target_attempts
            starts.append((request.path, now))
            if request.path == "/robots.txt":
                return web.Response(text="User-agent: *\nCrawl-delay: 2")
            if request.path == "/start":
                raise web.HTTPFound("/target")
            target_attempts += 1
            if target_attempts == 1:
                return web.Response(status=503)
            return web.Response(text="Target")

        limiter = RateLimiter(1, clock=read_clock, sleep=advance_clock)
        with patch("crawler.core.RateLimiter", return_value=limiter):
            crawler = AsyncCrawler(
                max_concurrent=1,
                requests_per_second=1,
                respect_robots=True,
                retry_strategy=RetryStrategy(max_retries=1, initial_delay=0),
            )
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/{path:.*}", respond)

        with patch.object(crawler, "fetch_robots", wraps=crawler.fetch_robots) as check_rules:
            async with server:
                start = str(server.make_url("/start"))
                target = str(server.make_url("/target"))
                async with asyncio.timeout(15):
                    self.assertEqual(await crawler.fetch_url(start), "Target")

        self.assertEqual(check_rules.await_args_list, [call(start), call(target), call(target)])
        self.assertEqual(
            starts, [("/robots.txt", 0.0), ("/start", 2.0), ("/target", 4.0), ("/target", 6.0)]
        )
        stats = crawler.get_stats()
        self.assertEqual(stats["http_attempts"], 4)
        self.assertEqual(stats["processed_pages"], 1)
        self.assertEqual(stats["successful_retries"], 1)

    async def test_crawl_counts_attempts_separately_from_page_results(self) -> None:
        now = 0.0
        attempts = 0

        def read_clock() -> float:
            return now

        async def wait_before_retry(delay: float) -> None:
            nonlocal now
            now += delay

        async def flaky_page(_request: web.Request) -> web.Response:
            nonlocal attempts, now
            attempts += 1
            if attempts == 1:
                now += 1.0
                return web.Response(status=503, headers={"Retry-After": "5"})
            now += 2.5
            return web.Response(text="<h1>Recovered</h1>", content_type="text/html")

        crawler = AsyncCrawler(
            max_concurrent=1,
            retry_strategy=RetryStrategy(max_retries=1, sleep=wait_before_retry),
        )
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/flaky", flaky_page)

        with (
            patch("crawler.core.perf_counter", side_effect=read_clock),
            self.assertLogs("crawler.core", level="INFO") as captured,
        ):
            async with server:
                flaky = str(server.make_url("/flaky"))
                missing = str(server.make_url("/missing"))
                pages = await crawler.crawl([flaky, missing])

        self.assertEqual(list(pages), [flaky])
        self.assertEqual(set(crawler.failed_urls), {missing})
        stats = crawler.get_stats()
        self.assertEqual(
            stats,
            {
                "http_attempts": 3,
                "processed_pages": 1,
                "failed_pages": 1,
                "blocked_pages": 0,
                "errors_by_type": {"TransientError": 1, "PermanentError": 1},
                "successful_retries": 1,
                "average_retry_duration": 2.5,
                "permanent_errors": {missing: str(crawler.failed_urls[missing])},
                "saved_pages": 0,
                "storage_errors": {},
                "storage_close_error": None,
            },
        )

        events = [
            (record.event, record.attempt, getattr(record, "status_code", None))
            for record in captured.records
            if record.url == flaky
        ]
        self.assertEqual(
            events,
            [
                ("fetch_started", 1, None),
                ("fetch_failed", 1, 503),
                ("fetch_retry_scheduled", 2, None),
                ("fetch_started", 2, None),
                ("fetch_succeeded", 2, 200),
            ],
        )
        retry = next(record for record in captured.records if record.event == "fetch_retry_scheduled")
        self.assertEqual(retry.retry_delay, 5.0)
        self.assertEqual(retry.error_type, "ClientResponseError")

        stats["errors_by_type"].clear()
        stats["permanent_errors"].clear()
        self.assertEqual(crawler.get_stats()["errors_by_type"]["TransientError"], 1)
        self.assertIn(missing, crawler.get_stats()["permanent_errors"])

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

    async def test_session_uses_configured_timeouts(self) -> None:
        crawler = AsyncCrawler(connect_timeout=2.0, read_timeout=3.0, total_timeout=8.0)
        self.addAsyncCleanup(crawler.close)

        session = crawler._get_session()

        self.assertEqual(session.timeout.connect, 2.0)
        self.assertEqual(session.timeout.sock_read, 3.0)
        self.assertEqual(session.timeout.total, 8.0)

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

    def test_max_per_host_must_be_positive_when_set(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_per_host"):
            AsyncCrawler(max_per_host=0)

    async def test_request_rate_is_applied_after_waiting_for_a_network_slot(self) -> None:
        now = 0.0
        starts: dict[str, float] = {}

        def read_clock() -> float:
            return now

        async def advance_clock(delay: float) -> None:
            nonlocal now
            now += delay

        async def timed_page(request: web.Request) -> web.Response:
            nonlocal now
            name = request.match_info["name"]
            starts[name] = now
            if name == "slow":
                now = 2.0
            return web.Response(text=name)

        limiter = RateLimiter(2, per_domain=False, clock=read_clock, sleep=advance_clock)
        with patch("crawler.core.RateLimiter", return_value=limiter) as make_limiter:
            crawler = AsyncCrawler(max_concurrent=1, requests_per_second=2, per_domain=False)
        make_limiter.assert_called_once_with(
            2, per_domain=False, min_delay=0.0, jitter=0.0
        )
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/timed/{name}", timed_page)

        async with server:
            urls = [str(server.make_url(f"/timed/{name}")) for name in ("slow", "two", "three")]
            pages = await asyncio.wait_for(crawler.fetch_urls(urls), timeout=5)

        self.assertEqual(set(pages), set(urls))
        self.assertEqual(starts, {"slow": 0.0, "two": 2.0, "three": 2.5})

    async def test_fetch_urls_applies_global_and_per_host_limits(self) -> None:
        active_by_host = {"127.0.0.1": 0, "localhost": 0}
        peak_by_host = dict(active_by_host)
        peak_total = 0
        three_started = asyncio.Event()
        allow_responses = asyncio.Event()
        two_localhost_requests_started = asyncio.Event()

        async def measured_page(request: web.Request) -> web.Response:
            nonlocal peak_total
            host = request.url.host
            active_by_host[host] += 1
            peak_by_host[host] = max(peak_by_host[host], active_by_host[host])
            peak_total = max(peak_total, sum(active_by_host.values()))
            if peak_total >= 3:
                three_started.set()
            if active_by_host["localhost"] >= 2:
                two_localhost_requests_started.set()

            try:
                await allow_responses.wait()
                if host == "localhost":
                    await two_localhost_requests_started.wait()
                await asyncio.sleep(0.05)
                return web.Response(text="Page")
            finally:
                active_by_host[host] -= 1

        crawler = AsyncCrawler(max_concurrent=3, max_per_host=2)
        self.addAsyncCleanup(crawler.close)
        server = make_test_server()
        server.app.router.add_get("/measured/{number}", measured_page)

        async with server:
            urls = [
                str(server.make_url(f"/measured/{number}").with_host(host))
                for host in ("127.0.0.1", "localhost")
                for number in range(3)
            ]
            task = asyncio.create_task(crawler.fetch_urls(urls))
            try:
                await asyncio.wait_for(three_started.wait(), timeout=5)
                self.assertEqual(crawler.active_requests, 3)
                allow_responses.set()
                pages = await asyncio.wait_for(task, timeout=5)
            finally:
                allow_responses.set()
                two_localhost_requests_started.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(set(pages), set(urls))
        self.assertEqual(peak_total, 3)
        self.assertEqual(peak_by_host, {"127.0.0.1": 2, "localhost": 2})
        self.assertEqual(crawler.active_requests, 0)

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
        self.assertEqual(crawler.active_requests, 0)

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
