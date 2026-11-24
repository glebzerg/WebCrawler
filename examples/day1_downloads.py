import asyncio
import logging
import sys
import time

from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler import AsyncCrawler


async def delayed_page(request: web.Request) -> web.Response:
    await asyncio.sleep(0.2)
    return web.Response(text=f"Page {request.match_info['number']}")


async def run_downloads(
    urls: list[str], max_concurrent: int
) -> tuple[dict[str, str], float]:
    crawler = AsyncCrawler(max_concurrent=max_concurrent)
    try:
        print(f"\nDownload limit: {max_concurrent}")
        started_at = time.perf_counter()
        pages = await crawler.fetch_urls(urls)
        elapsed = time.perf_counter() - started_at

        for url in urls:
            if url in pages:
                print(f"  OK {url}")
            else:
                print(f"  FAILED {url} ({crawler.failed_urls[url]})")

        print(f"Total time: {elapsed:.3f} s")
        return pages, elapsed
    finally:
        await crawler.close()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(relativeCreated)7.0f ms  %(message)s",
        stream=sys.stdout,
    )
    app = web.Application()
    app.router.add_get("/pages/{number}", delayed_page)
    async with TestServer(app, host="127.0.0.1", port=0) as server:
        urls = [str(server.make_url(f"/pages/{number}")) for number in range(1, 7)]
        sequential_pages, sequential_time = await run_downloads(urls, max_concurrent=1)
        concurrent_pages, concurrent_time = await run_downloads(urls, max_concurrent=3)

    if len(sequential_pages) != len(urls) or sequential_pages != concurrent_pages:
        raise RuntimeError("Both runs must download the same six pages")

    print("\nBoth runs returned the same six pages.")
    print(f"Speedup in this run: {sequential_time / concurrent_time:.2f}x")


if __name__ == "__main__":
    asyncio.run(main())
