import socket
import unittest

from aiohttp import ClientSession, ClientTimeout

from tests.http_server import make_test_server


class LocalHttpServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_ok_response_and_port_cleanup(self) -> None:
        async with make_test_server() as server:
            address = (server.host, server.port)
            url = server.make_url("/ok")
            async with ClientSession(timeout=ClientTimeout(total=5)) as client:
                async with client.get(url) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(await response.text(), "Hello from the test server")

        with socket.socket() as port_probe:
            port_probe.bind(address)
