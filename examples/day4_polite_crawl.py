import asyncio
from time import perf_counter

from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler import AsyncCrawler, RobotsDisallowedError


ROBOTS = """User-agent: PoliteDemoBot
Disallow: /private
Crawl-delay: 0.2
"""
PAGES = {
    "/": """
        <head><title>Polite crawl demo</title></head>
        <body>
            <a href="/public/one">First public page</a>
            <a href="/private">Private page</a>
            <a href="/public/two">Second public page</a>
        </body>
    """,
    "/public/one": "<body>First public page</body>",
    "/public/two": "<body>Second public page</body>",
    "/private": "<body>Private page</body>",
}


async def main() -> None:
    requests: list[tuple[float, str]] = []

    async def local_page(request: web.Request) -> web.Response:
        requests.append((perf_counter(), request.path))
        if request.path == "/robots.txt":
            return web.Response(text=ROBOTS)
        html = PAGES.get(request.path)
        if html is None:
            raise web.HTTPNotFound()
        return web.Response(text=html, content_type="text/html")

    app = web.Application()
    app.router.add_get("/{path:.*}", local_page)

    async with TestServer(app, host="127.0.0.1", port=0) as server:
        crawler = AsyncCrawler(
            max_concurrent=3,
            requests_per_second=10,
            min_delay=0.15,
            user_agent="PoliteDemoBot/0.1",
            respect_robots=True,
        )
        try:
            started_at = perf_counter()
            pages = await crawler.crawl(
                [str(server.make_url("/"))],
                max_depth=1,
                same_domain_only=True,
            )
            elapsed = perf_counter() - started_at
        finally:
            await crawler.close()

    blocked = {
        error.url
        for error in crawler.failed_urls.values()
        if isinstance(error, RobotsDisallowedError)
    }
    for start, path in requests:
        print(f"{start - started_at:.3f} s  GET {path}")
    for url in sorted(blocked):
        print(f"Blocked by robots.txt: {url}")

    request_count = len(requests)
    requests_per_second = request_count / elapsed if elapsed > 0 else 0.0
    mean_interval = (
        (requests[-1][0] - requests[0][0]) / (request_count - 1)
        if request_count > 1
        else 0.0
    )
    print(f"HTTP requests: {request_count}")
    print(f"Elapsed: {elapsed:.3f} s")
    print(f"Request rate: {requests_per_second:.2f} req/s")
    print(f"Mean start interval: {mean_interval:.3f} s")
    print(f"Pages downloaded: {len(pages)}")
    print(f"Blocked URLs: {len(blocked)}")


if __name__ == "__main__":
    asyncio.run(main())
