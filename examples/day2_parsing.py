import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler import AsyncCrawler


PAGES = {
    "guide": """
        <head><title>Crawler guide</title></head>
        <body>
            <main>
                <h1>Getting started</h1>
                <p>Fetch and parse a local page.</p>
                <a href="reference">Reference</a>
                <img src="../assets/diagram.png" alt="Crawler diagram">
            </main>
        </body>
    """,
    "reference": """
        <head><title>Library reference</title></head>
        <body>
            <section>
                <h2>Libraries</h2>
                <ul><li>aiohttp</li><li>Beautiful Soup</li></ul>
                <a href="guide">Back to guide</a>
                <a href="https://example.org/docs">External docs</a>
            </section>
        </body>
    """,
}


async def local_page(request: web.Request) -> web.Response:
    html = PAGES.get(request.match_info["name"])
    if html is None:
        raise web.HTTPNotFound()
    return web.Response(text=html, content_type="text/html")


async def main() -> None:
    app = web.Application()
    app.router.add_get("/pages/{name}", local_page)

    async with TestServer(app, host="127.0.0.1", port=0) as server:
        urls = [str(server.make_url(f"/pages/{name}")) for name in PAGES]
        crawler = AsyncCrawler(max_concurrent=2)
        try:
            pages = await asyncio.gather(*(crawler.fetch_and_parse(url) for url in urls))
        finally:
            await crawler.close()

    summaries = [
        {
            "url": page["url"],
            "title": page["title"],
            "headings": page["headings"],
            "text_length": len(page["text"]),
            "links_count": len(page["links"]),
            "links": page["links"],
            "images_count": len(page["images"]),
        }
        for page in pages
    ]
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
