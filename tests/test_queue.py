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

    async def test_same_url_key_is_queued_only_once(self) -> None:
        queue = CrawlerQueue()
        first = "HTTPS://Example.COM/a#top"
        duplicate = "https://example.com/a#end"
        queue.add_url(first)
        queue.add_url(duplicate, priority=10)

        self.assertEqual(queue.visited_urls, {"https://example.com/a"})
        self.assertEqual(queue.get_stats()["queued"], 1)
        self.assertEqual(await queue.get_next(), first)

        queue.add_url(duplicate)
        self.assertIsNone(await queue.get_next())
        queue.mark_processed(first)
        queue.add_url(duplicate)
        self.assertIsNone(await queue.get_next())

    async def test_records_one_outcome_per_url_and_reports_stats(self) -> None:
        queue = CrawlerQueue()
        ok = "https://example.com/ok"
        failed = "https://example.com/failed"
        queue.add_url(ok)
        queue.add_url(failed)

        self.assertEqual(await queue.get_next(), ok)
        self.assertEqual(
            queue.get_stats(),
            {"queued": 1, "in_progress": 1, "processed": 0, "failed": 0, "visited": 2},
        )
        with self.assertRaisesRegex(ValueError, "not in progress"):
            queue.mark_failed(failed, "HTTP 500")

        queue.mark_processed(ok)
        self.assertEqual(await queue.get_next(), failed)
        queue.mark_failed(failed, "HTTP 500")

        with self.assertRaisesRegex(ValueError, "not in progress"):
            queue.mark_failed(ok, "Late failure")
        with self.assertRaisesRegex(ValueError, "not in progress"):
            queue.mark_processed(failed)

        self.assertEqual(queue.processed_urls, {ok: None})
        self.assertEqual(queue.failed_urls, {failed: "HTTP 500"})
        self.assertEqual(
            queue.get_stats(),
            {"queued": 0, "in_progress": 0, "processed": 1, "failed": 1, "visited": 2},
        )
