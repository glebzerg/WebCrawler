import asyncio
import json
from pathlib import Path

import aiofiles
import aiofiles.os
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler import AsyncCrawler, CrawlProgress


PAGES = {
    "/": """
        <head><title>Crawl demo</title></head>
        <body><a href="/guide">Guide</a><a href="/missing">Missing page</a></body>
    """,
    "/guide": """
        <head><title>Guide</title></head>
        <body><a href="/#top">Start</a><a href="/reference">Reference</a></body>
    """,
    "/reference": """
        <head><title>Reference</title></head>
        <body><p>One more page discovered during the crawl.</p></body>
    """,
}
OUTPUT = Path("results") / "day3-crawl.json"


async def main() -> None:
    requests: list[str] = []
    final_progress: CrawlProgress | None = None

    async def local_page(request: web.Request) -> web.Response:
        requests.append(request.path)
        await asyncio.sleep(0.1)
        html = PAGES.get(request.path)
        if html is None:
            raise web.HTTPNotFound()
        return web.Response(text=html, content_type="text/html")

    def show_progress(stats: CrawlProgress) -> None:
        nonlocal final_progress
        final_progress = stats
        print(
            f"Processed: {stats['processed']} | queued: {stats['queued']} | "
            f"in progress: {stats['in_progress']} | errors: {stats['failed']} | "
            f"{stats['pages_per_second']:.2f} pages/s",
            flush=True,
        )

    app = web.Application()
    app.router.add_get("/{path:.*}", local_page)

    async with TestServer(app, host="127.0.0.1", port=0) as server:
        crawler = AsyncCrawler(max_concurrent=2, max_per_host=2)
        try:
            pages = await crawler.crawl(
                [str(server.make_url("/"))],
                max_depth=2,
                same_domain_only=True,
                on_progress=show_progress,
            )
        finally:
            await crawler.close()

    report = {
        "stats": final_progress,
        "pages": list(pages.values()),
        "errors": {url: str(error) for url, error in crawler.failed_urls.items()},
    }
    await aiofiles.os.makedirs(OUTPUT.parent, exist_ok=True)
    async with aiofiles.open(OUTPUT, "w", encoding="utf-8") as file:
        await file.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(f"Requests: {' -> '.join(requests)}")
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
