import asyncio
import unittest

from crawler.robots_cache import RobotsCache, RobotsUnavailableError


class RobotsCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_waiter_does_not_cancel_the_shared_load(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        requests = []

        async def fetch_text(url: str) -> str:
            requests.append(url)
            started.set()
            await release.wait()
            return "User-agent: *\nDisallow: /private/"

        cache = RobotsCache(fetch_text)
        self.addAsyncCleanup(cache.close)
        first = asyncio.create_task(cache.get("https://EXAMPLE.com:443/first"))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
        finally:
            release.set()

        rules = await cache.get("https://example.com/second")

        self.assertFalse(rules.can_fetch("/private/page"))
        self.assertEqual(requests, ["https://example.com/robots.txt"])

    async def test_failed_download_is_reused_until_retry_delay_expires(self) -> None:
        attempts = 0
        now = 0.0

        async def fetch_text(_url: str) -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("Temporary failure")
            return "User-agent: *\nDisallow: /private/"

        cache = RobotsCache(fetch_text, clock=lambda: now)
        self.addAsyncCleanup(cache.close)

        with self.assertRaisesRegex(RobotsUnavailableError, "TimeoutError") as raised:
            await cache.get("https://example.com/first")
        self.assertIsInstance(raised.exception.__cause__, TimeoutError)
        self.assertEqual(raised.exception.url, "https://example.com/robots.txt")

        now = 29.0
        with self.assertRaises(RobotsUnavailableError):
            await cache.get("https://example.com/second")
        self.assertEqual(attempts, 1)

        now = 30.0
        rules = await cache.get("https://example.com/second")

        self.assertFalse(rules.can_fetch("/private/page"))
        self.assertEqual(attempts, 2)
