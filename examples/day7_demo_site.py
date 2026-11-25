import argparse

from aiohttp import web


PAGES = {
    "/": (
        "<title>Home</title><h1>Local crawler demo</h1>"
        '<a href="/guide">Guide</a> <a href="/reference">Reference</a> '
        '<a href="/private">Private page</a>'
    ),
    "/guide": '<title>Guide</title><p>Guide page</p><a href="/">Home</a>',
    "/reference": '<title>Reference</title><p>Reference page</p><a href="/">Home</a>',
    "/private": "<title>Private</title><p>Excluded by robots.txt</p>",
}


def make_app(port: int) -> web.Application:
    async def serve(request: web.Request) -> web.Response:
        if request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nDisallow: /private\n")
        if request.path == "/sitemap.xml":
            sitemap = (
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>http://127.0.0.1:{port}/</loc></url></urlset>"
            )
            return web.Response(text=sitemap, content_type="application/xml")
        page = PAGES.get(request.path)
        if page is None:
            raise web.HTTPNotFound()
        return web.Response(text=page, content_type="text/html")

    app = web.Application()
    app.router.add_get("/{path:.*}", serve)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local WebCrawler quickstart site")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    web.run_app(make_app(args.port), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
