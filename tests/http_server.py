from aiohttp import web
from aiohttp.test_utils import TestServer


async def ok(_request: web.Request) -> web.Response:
    return web.Response(text="Hello from the test server")


def make_test_server() -> TestServer:
    app = web.Application()
    app.router.add_get("/ok", ok)
    return TestServer(app, host="127.0.0.1", port=0)
