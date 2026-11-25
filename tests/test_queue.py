import unittest

from crawler import CrawlerQueue


class CrawlerQueueTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_next_prefers_priority_then_insertion_order(self) -> None:
        queue = CrawlerQueue()
        first = "https://example.com/z"
        second = "https://example.com/a"
        urgent = "https://example.com/urgent"
        queue.add_url(first)
        queue.add_url(second)
        queue.add_url(urgent, priority=10)

        self.assertEqual(await queue.get_next(), urgent)
        self.assertEqual(await queue.get_next(), first)
        self.assertEqual(await queue.get_next(), second)
        self.assertIsNone(await queue.get_next())

    async def test_get_next_returns_none_for_an_empty_queue(self) -> None:
        self.assertIsNone(await CrawlerQueue().get_next())
