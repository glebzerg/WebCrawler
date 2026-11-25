import unittest

from crawler import PermanentError, RetryStrategy


class RetryStrategyTest(unittest.IsolatedAsyncioTestCase):
    async def test_three_retries_make_four_attempts_and_preserve_the_final_error(self) -> None:
        attempts = 0
        final_error = TimeoutError("Read timed out")

        async def load() -> str:
            nonlocal attempts
            attempts += 1
            raise final_error

        with self.assertRaises(TimeoutError) as raised:
            await RetryStrategy(max_retries=3).execute_with_retry(load)

        self.assertEqual(attempts, 4)
        self.assertIs(raised.exception, final_error)

    async def test_successful_retry_receives_the_original_arguments(self) -> None:
        calls = []

        async def load(url: str, *, prefix: str) -> str:
            calls.append((url, prefix))
            if len(calls) == 1:
                raise ConnectionRefusedError("Connection refused")
            return f"{prefix} {url}"

        url = "https://example.com/page"
        result = await RetryStrategy().execute_with_retry(load, url, prefix="Loaded")

        self.assertEqual(result, f"Loaded {url}")
        self.assertEqual(calls, [(url, "Loaded"), (url, "Loaded")])

    async def test_permanent_error_is_not_retried_by_default(self) -> None:
        attempts = 0

        async def load() -> None:
            nonlocal attempts
            attempts += 1
            raise PermanentError("HTTP 404")

        with self.assertRaisesRegex(PermanentError, "404"):
            await RetryStrategy().execute_with_retry(load)

        self.assertEqual(attempts, 1)

    async def test_retry_on_can_enable_a_custom_error_or_disable_retries(self) -> None:
        for retry_on, expected_attempts in (((ValueError,), 2), ((), 1)):
            with self.subTest(retry_on=retry_on):
                attempts = 0

                async def load() -> None:
                    nonlocal attempts
                    attempts += 1
                    raise ValueError("Custom failure")

                strategy = RetryStrategy(max_retries=1, retry_on=retry_on)
                with self.assertRaisesRegex(ValueError, "Custom failure"):
                    await strategy.execute_with_retry(load)

                self.assertEqual(attempts, expected_attempts)

    def test_negative_retry_budget_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_retries"):
            RetryStrategy(max_retries=-1)
