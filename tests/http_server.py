from aiohttp import web
from aiohttp.test_utils import TestServer


async def ok(_request: web.Request) -> web.Response:
    return web.Response(text="Hello from the test server")


async def empty(_request: web.Request) -> web.Response:
    return web.Response(text="")


async def server_error(_request: web.Request) -> web.Response:
    return web.Response(status=500, text="Internal server error")


def make_test_server() -> TestServer:
    app = web.Application()
    app.router.add_get("/ok", ok)
    app.router.add_get("/empty", empty)
    app.router.add_get("/server-error", server_error)
    return TestServer(app, host="127.0.0.1", port=0)
