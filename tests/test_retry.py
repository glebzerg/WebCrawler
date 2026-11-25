import unittest
from unittest.mock import AsyncMock, call

from crawler import BackoffPolicy, NetworkError, PermanentError, RetryStrategy


class BackoffPolicyTest(unittest.TestCase):
    def test_exponential_delay_is_capped(self) -> None:
        policy = BackoffPolicy(initial_delay=1.0, factor=2.0, max_delay=3.0)

        self.assertEqual([policy.delay_for(n) for n in (1, 2, 3, 4)], [1.0, 2.0, 3.0, 3.0])
        self.assertEqual(policy.delay_for(10_000), 3.0)

    def test_invalid_policy_or_retry_number_is_rejected(self) -> None:
        for field, value in (("initial_delay", -1), ("factor", 0.5), ("max_delay", float("inf"))):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                BackoffPolicy(**{field: value})

        with self.assertRaisesRegex(ValueError, "retry_number"):
            BackoffPolicy().delay_for(0)


class RetryStrategyTest(unittest.IsolatedAsyncioTestCase):
    async def test_three_retries_make_four_attempts_and_preserve_the_final_error(self) -> None:
        attempts = 0
        final_error = TimeoutError("Read timed out")
        sleep = AsyncMock()

        async def load() -> str:
            nonlocal attempts
            attempts += 1
            raise final_error

        strategy = RetryStrategy(max_retries=3, max_delay=3.0, sleep=sleep)
        with self.assertRaises(TimeoutError) as raised:
            await strategy.execute_with_retry(load)

        self.assertEqual(attempts, 4)
        self.assertIs(raised.exception, final_error)
        self.assertEqual(sleep.await_args_list, [call(1.0), call(2.0), call(3.0)])

    async def test_successful_retry_receives_the_original_arguments(self) -> None:
        calls = []

        async def load(url: str, *, prefix: str) -> str:
            calls.append((url, prefix))
            if len(calls) == 1:
                raise ConnectionRefusedError("Connection refused")
            return f"{prefix} {url}"

        url = "https://example.com/page"
        sleep = AsyncMock()
        result = await RetryStrategy(sleep=sleep).execute_with_retry(load, url, prefix="Loaded")

        self.assertEqual(result, f"Loaded {url}")
        self.assertEqual(calls, [(url, "Loaded"), (url, "Loaded")])
        sleep.assert_awaited_once_with(1.0)

    async def test_permanent_error_is_not_retried_by_default(self) -> None:
        attempts = 0
        sleep = AsyncMock()

        async def load() -> None:
            nonlocal attempts
            attempts += 1
            raise PermanentError("HTTP 404")

        with self.assertRaisesRegex(PermanentError, "404"):
            await RetryStrategy(sleep=sleep).execute_with_retry(load)

        self.assertEqual(attempts, 1)
        sleep.assert_not_awaited()

    async def test_retry_on_can_enable_a_custom_error_or_disable_retries(self) -> None:
        for retry_on, expected_attempts in (((ValueError,), 2), ((), 1)):
            with self.subTest(retry_on=retry_on):
                attempts = 0

                async def load() -> None:
                    nonlocal attempts
                    attempts += 1
                    raise ValueError("Custom failure")

                strategy = RetryStrategy(max_retries=1, retry_on=retry_on, sleep=AsyncMock())
                with self.assertRaisesRegex(ValueError, "Custom failure"):
                    await strategy.execute_with_retry(load)

                self.assertEqual(attempts, expected_attempts)

    async def test_backoff_uses_the_policy_for_each_failure(self) -> None:
        load = AsyncMock(side_effect=[ConnectionRefusedError(), TimeoutError(), "success"])
        sleep = AsyncMock()
        network_backoff = BackoffPolicy(initial_delay=0.25, factor=3.0, max_delay=0.5)
        strategy = RetryStrategy(
            backoff_factor=4.0,
            backoff_by_error={NetworkError: network_backoff},
            sleep=sleep,
        )

        self.assertEqual(await strategy.execute_with_retry(load), "success")
        self.assertEqual(sleep.await_args_list, [call(0.25), call(4.0)])

    async def test_zero_delay_allows_a_retry_without_sleep(self) -> None:
        load = AsyncMock(side_effect=[TimeoutError(), "success"])
        sleep = AsyncMock()
        strategy = RetryStrategy(initial_delay=0.0, sleep=sleep)

        self.assertEqual(await strategy.execute_with_retry(load), "success")
        self.assertEqual(load.await_count, 2)
        sleep.assert_not_awaited()

    def test_negative_retry_budget_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_retries"):
            RetryStrategy(max_retries=-1)
