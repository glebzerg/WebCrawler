import asyncio
import unittest
from unittest.mock import AsyncMock

from aiohttp import web

from crawler import AsyncCrawler, CrawlProgress, SitemapParser
from tests.http_server import make_test_server


class SitemapParserTest(unittest.IsolatedAsyncioTestCase):
    async def test_reads_absolute_locations_in_the_sitemap_namespace(self) -> None:
        sitemap_url = "https://example.test/sitemap.xml"
        fetch = AsyncMock(return_value="""<?xml version="1.0" encoding="UTF-8"?>
            <site:urlset xmlns:site="http://www.sitemaps.org/schemas/sitemap/0.9"
                         xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
              <site:url>
                <site:loc>https://example.test/catalog?one=1&amp;two=2</site:loc>
                <image:image><image:loc>https://example.test/photo.jpg</image:loc></image:image>
              </site:url>
              <site:url><site:loc>/relative</site:loc></site:url>
              <site:url><site:loc>ftp://example.test/file</site:loc></site:url>
              <site:url><site:loc /></site:url>
            </site:urlset>
        """)

        urls = await SitemapParser(fetch).fetch_sitemap(sitemap_url)

        self.assertEqual(urls, ["https://example.test/catalog?one=1&two=2"])
        fetch.assert_awaited_once_with(sitemap_url)

    async def test_crawl_enqueues_unique_sitemap_urls_without_using_page_budget(self) -> None:
        server = make_test_server()
        requests: list[str] = []
        progress: list[CrawlProgress] = []

        async def sitemap(request: web.Request) -> web.Response:
            requests.append(request.path)
            first = server.make_url("/pages/one")
            second = server.make_url("/pages/two")
            xml = f"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>{first}</loc></url>
              <url><loc>{first}#section</loc></url>
              <url><loc>{second}</loc></url>
            </urlset>"""
            return web.Response(text=xml, content_type="application/xml")

        async def page(request: web.Request) -> web.Response:
            requests.append(request.path)
            return web.Response(text="<a href='/pages/other'>Other</a>", content_type="text/html")

        server.app.router.add_get("/sitemap.xml", sitemap)
        server.app.router.add_get("/pages/{name}", page)
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        async with server:
            first = str(server.make_url("/pages/one"))
            second = str(server.make_url("/pages/two"))
            pages = await crawler.crawl(
                sitemap_urls=[str(server.make_url("/sitemap.xml"))],
                max_pages=2,
                max_depth=0,
                same_domain_only=True,
                include_patterns=("*/pages/*",),
                on_progress=progress.append,
            )

        self.assertEqual(set(pages), {first, second})
        self.assertEqual(progress[0]["queued"], 2)
        self.assertEqual(requests[0], "/sitemap.xml")
        self.assertCountEqual(requests[1:], ["/pages/one", "/pages/two"])
        self.assertEqual(crawler.get_stats()["http_attempts"], 3)
        self.assertEqual(crawler.get_stats()["processed_pages"], 2)

    async def test_crawl_logs_invalid_sitemap_and_still_fetches_manual_seed(self) -> None:
        async def broken_sitemap(_request: web.Request) -> web.Response:
            return web.Response(text="<urlset>", content_type="application/xml")

        server = make_test_server()
        server.app.router.add_get("/broken.xml", broken_sitemap)
        crawler = AsyncCrawler()
        self.addAsyncCleanup(crawler.close)

        async with server:
            sitemap_url = str(server.make_url("/broken.xml"))
            page_url = str(server.make_url("/ok"))
            with self.assertLogs("crawler.core", level="WARNING") as captured:
                pages = await crawler.crawl([page_url], sitemap_urls=[sitemap_url])

        self.assertEqual(list(pages), [page_url])
        self.assertEqual(crawler.failed_urls, {})
        self.assertEqual(captured.records[0].event, "sitemap_failed")
        self.assertEqual(captured.records[0].url, sitemap_url)

    async def test_crawl_expands_cyclic_indexes_with_scope_robots_and_page_limits(self) -> None:
        server = make_test_server()
        requested: list[str] = []
        namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"

        async def respond(request: web.Request) -> web.Response:
            path = request.path
            requested.append(path)
            if path == "/robots.txt":
                return web.Response(text="User-agent: *\nDisallow: /blocked.xml")
            if path in {"/index-a.xml", "/index-b.xml"}:
                if path == "/index-a.xml":
                    children = ["/index-b.xml", "/blocked.xml", "/broken.xml", "/pages.xml"]
                    locations = [str(server.make_url(child)) for child in children]
                    locations.append(str(server.make_url("/offsite.xml").with_host("localhost")))
                else:
                    locations = [
                        f"{server.make_url('/index-a.xml')}#cycle",
                        str(server.make_url("/pages.xml")),
                    ]
                entries = "".join(f"<sitemap><loc>{url}</loc></sitemap>" for url in locations)
                xml = f'<sitemapindex xmlns="{namespace}">{entries}</sitemapindex>'
                return web.Response(text=xml, content_type="application/xml")
            if path == "/broken.xml":
                return web.Response(text="<broken>", content_type="application/xml")
            if path == "/pages.xml":
                paths = ("/private", "/public/excluded", "/public/one", "/public/two", "/public/extra")
                entries = "".join(f"<url><loc>{server.make_url(url)}</loc></url>" for url in paths)
                return web.Response(
                    text=f'<urlset xmlns="{namespace}">{entries}</urlset>',
                    content_type="application/xml",
                )
            return web.Response(text="<p>Page</p>", content_type="text/html")

        server.app.router.add_get("/{path:.*}", respond)
        crawler = AsyncCrawler(max_concurrent=1, respect_robots=True)
        self.addAsyncCleanup(crawler.close)

        async with server:
            to_visit = [str(server.make_url(path)) for path in ("/public/one", "/public/two")]
            failed_maps = {str(server.make_url(path)) for path in ("/blocked.xml", "/broken.xml")}
            with self.assertLogs("crawler.core", level="WARNING") as captured:
                async with asyncio.timeout(15):
                    pages = await crawler.crawl(
                        sitemap_urls=[
                            str(server.make_url("/index-a.xml")),
                            str(server.make_url("/index-b.xml")),
                        ],
                        max_pages=2,
                        max_depth=0,
                        same_domain_only=True,
                        include_patterns=("*/public/*",),
                        exclude_patterns=("*/excluded",),
                    )

        self.assertEqual(list(pages), to_visit)
        self.assertEqual(
            requested,
            [
                "/robots.txt",
                "/index-a.xml",
                "/index-b.xml",
                "/pages.xml",
                "/broken.xml",
                "/public/one",
                "/public/two",
            ],
        )
        self.assertEqual(crawler.get_stats()["http_attempts"], len(requested))
        self.assertEqual(crawler.get_stats()["processed_pages"], 2)
        self.assertEqual(crawler.failed_urls, {})
        self.assertEqual(
            {record.url for record in captured.records if record.event == "sitemap_failed"},
            failed_maps,
        )
