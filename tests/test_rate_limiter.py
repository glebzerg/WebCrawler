import asyncio
import unittest

from crawler import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.delays: list[float] = []

    def read(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)
        self.now += delay


class RateLimiterTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_starts_are_spaced_at_the_configured_rate(self) -> None:
        clock = FakeClock()
        limiter = RateLimiter(2, clock=clock.read, sleep=clock.sleep)

        async def record_start() -> float:
            await limiter.acquire()
            return clock.read()

        starts = await asyncio.gather(*(record_start() for _ in range(3)))

        self.assertEqual(starts, [0.0, 0.5, 1.0])
        self.assertEqual(clock.delays, [0.5, 0.5])

    async def test_domains_can_share_a_schedule_or_use_separate_ones(self) -> None:
        for per_domain, expected in (
            (True, [("a.test", 0.0), ("b.test", 0.0), ("a.test", 0.5)]),
            (False, [("a.test", 0.0), ("a.test", 0.5), ("b.test", 1.0)]),
        ):
            with self.subTest(per_domain=per_domain):
                clock = FakeClock()
                limiter = RateLimiter(
                    2, per_domain=per_domain, clock=clock.read, sleep=clock.sleep
                )
                starts = []

                async def record_start(domain: str) -> None:
                    await limiter.acquire(domain)
                    starts.append((domain, clock.read()))

                domains = ("a.test", "a.test", "b.test")
                await asyncio.gather(*(record_start(domain) for domain in domains))

                self.assertEqual(starts, expected)

    async def test_idle_time_does_not_accumulate_extra_starts(self) -> None:
        clock = FakeClock()
        limiter = RateLimiter(2, clock=clock.read, sleep=clock.sleep)
        await limiter.acquire()

        clock.now = 10.0
        await limiter.acquire()
        self.assertEqual(clock.read(), 10.0)
        self.assertEqual(clock.delays, [])

        await limiter.acquire()
        self.assertEqual(clock.read(), 10.5)
        self.assertEqual(clock.delays, [0.5])

    def test_rejects_invalid_request_rates(self) -> None:
        for rate in (0.0, -1.0, float("inf"), float("nan"), 5e-324):
            with self.subTest(rate=rate):
                with self.assertRaisesRegex(ValueError, "requests_per_second"):
                    RateLimiter(rate)
