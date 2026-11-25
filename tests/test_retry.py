import unittest
from datetime import UTC, datetime
from functools import partial
from unittest.mock import AsyncMock, call, patch

from aiohttp import ClientResponseError, ClientTimeout, RequestInfo
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from crawler import (
    BackoffPolicy,
    NetworkError,
    PermanentError,
    RetryStrategy,
    RetryTimeoutPolicy,
    classify_error,
)


def http_error(status: int, retry_after: str | None) -> ClientResponseError:
    url = URL("https://example.com/page")
    request = RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict()), url)
    headers = None
    if retry_after is not None:
        headers = CIMultiDictProxy(CIMultiDict({"retry-after": retry_after}))
    return ClientResponseError(request, (), status=status, headers=headers)


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


class RetryTimeoutPolicyTest(unittest.TestCase):
    def test_preserves_disabled_limits_and_other_timeout_settings(self) -> None:
        base = ClientTimeout(total=None, connect=0, sock_read=2, sock_connect=4, ceil_threshold=7)
        policy = RetryTimeoutPolicy(factor=2.0, max_multiplier=3.0)

        self.assertEqual(
            policy.for_attempt(base, 10_000),
            ClientTimeout(total=None, connect=0, sock_read=6, sock_connect=4, ceil_threshold=7),
        )

    def test_invalid_policy_or_attempt_number_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "factor"):
            RetryTimeoutPolicy(factor=0.5)
        with self.assertRaisesRegex(ValueError, "max_multiplier"):
            RetryTimeoutPolicy(max_multiplier=float("inf"))
        with self.assertRaisesRegex(ValueError, "attempt_number"):
            RetryTimeoutPolicy().for_attempt(ClientTimeout(), 0)


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

    async def test_429_waits_for_retry_after_before_starting_the_next_attempt(self) -> None:
        load = AsyncMock(side_effect=[http_error(429, "5"), "success"])

        async def wait(seconds: float) -> None:
            self.assertEqual(seconds, 5.0)
            self.assertEqual(load.await_count, 1)

        sleep = AsyncMock(side_effect=wait)
        strategy = RetryStrategy(initial_delay=2.0, max_delay=3.0, sleep=sleep)

        self.assertEqual(await strategy.execute_with_retry(load), "success")
        sleep.assert_awaited_once_with(5.0)
        self.assertEqual(load.await_count, 2)

    async def test_retry_after_parses_http_dates_in_utc(self) -> None:
        for header, expected_delay in (
            ("Tue, 25 Nov 2025 10:00:07 GMT", 7.0),
            ("Tuesday, 25-Nov-25 10:00:07 GMT", 7.0),
            ("Tue Nov 25 10:00:07 2025", 7.0),
            ("Tue, 25 Nov 2025 09:59:59 GMT", 2.0),
        ):
            with self.subTest(header=header):
                error = classify_error(http_error(503, header))
                load = AsyncMock(side_effect=[error, "success"])
                sleep = AsyncMock()
                strategy = RetryStrategy(initial_delay=2.0, sleep=sleep)

                with patch("crawler.retry.datetime") as clock:
                    clock.now.return_value = datetime(2025, 11, 25, 10, 0, 0, tzinfo=UTC)
                    self.assertEqual(await strategy.execute_with_retry(load), "success")

                sleep.assert_awaited_once_with(expected_delay)

    async def test_missing_short_or_invalid_retry_after_uses_local_backoff(self) -> None:
        for header in (None, "", "0", "1", "-5", "1.5", "tomorrow", "9" * 400):
            with self.subTest(header=header):
                load = AsyncMock(side_effect=[http_error(503, header), "success"])
                sleep = AsyncMock()
                strategy = RetryStrategy(initial_delay=2.0, sleep=sleep)

                self.assertEqual(await strategy.execute_with_retry(load), "success")
                sleep.assert_awaited_once_with(2.0)

    async def test_retry_after_does_not_make_a_permanent_failure_retryable(self) -> None:
        error = http_error(404, "5")
        load = AsyncMock(side_effect=error)
        sleep = AsyncMock()

        with self.assertRaises(ClientResponseError) as raised:
            await RetryStrategy(sleep=sleep).execute_with_retry(load)

        self.assertIs(raised.exception, error)
        load.assert_awaited_once()
        sleep.assert_not_awaited()

    async def test_timeout_grows_to_the_cap_and_resets_for_the_next_url(self) -> None:
        first_url = "https://example.com/first"
        next_url = "https://example.com/next"
        attempts: list[tuple[str, ClientTimeout]] = []

        async def load(url: str, timeout: ClientTimeout) -> str:
            attempts.append((url, timeout))
            if url == first_url and len(attempts) < 3:
                raise TimeoutError("Read timed out")
            return url

        base = ClientTimeout(connect=2, sock_read=3, total=5)
        strategy = RetryStrategy(
            max_retries=2,
            timeout_policy=RetryTimeoutPolicy(factor=2.0, max_multiplier=3.0),
            sleep=AsyncMock(),
        )

        first_result = await strategy.execute_with_timeout(partial(load, first_url), base)
        next_result = await strategy.execute_with_timeout(partial(load, next_url), base)

        self.assertEqual((first_result, next_result), (first_url, next_url))
        self.assertEqual(
            attempts,
            [
                (first_url, ClientTimeout(connect=2, sock_read=3, total=5)),
                (first_url, ClientTimeout(connect=4, sock_read=6, total=10)),
                (first_url, ClientTimeout(connect=6, sock_read=9, total=15)),
                (next_url, ClientTimeout(connect=2, sock_read=3, total=5)),
            ],
        )

    def test_negative_retry_budget_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_retries"):
            RetryStrategy(max_retries=-1)
