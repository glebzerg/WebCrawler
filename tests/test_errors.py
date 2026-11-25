import unittest
from socket import gaierror

from aiohttp import ClientResponseError, RequestInfo, SocketTimeoutError
from bs4.exceptions import ParserRejectedMarkup
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from crawler import NetworkError, ParseError, PermanentError, TransientError, classify_error


class ErrorClassificationTest(unittest.TestCase):
    def test_http_statuses_keep_the_original_response_error(self) -> None:
        url = URL("https://example.com/page")
        request = RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict()), url)

        for status, expected in (
            (429, TransientError),
            (500, TransientError),
            (503, TransientError),
            (401, PermanentError),
            (403, PermanentError),
            (404, PermanentError),
        ):
            with self.subTest(status=status):
                original = ClientResponseError(request, (), status=status)
                classified = classify_error(original)

                self.assertIsInstance(classified, expected)
                self.assertIs(classified.__cause__, original)

    def test_timeouts_network_errors_and_parsing_errors_keep_their_causes(self) -> None:
        cases = (
            (TimeoutError("Timeout"), TransientError),
            (SocketTimeoutError("Read timed out"), TransientError),
            (ConnectionRefusedError("Connection refused"), NetworkError),
            (gaierror("DNS resolution failed"), NetworkError),
            (ParserRejectedMarkup("Malformed HTML"), ParseError),
            (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"), ParseError),
        )
        for original, expected in cases:
            with self.subTest(error=type(original).__name__):
                classified = classify_error(original)

                self.assertIsInstance(classified, expected)
                self.assertIs(classified.__cause__, original)
                self.assertIs(classify_error(classified), classified)

    def test_parsing_context_classifies_only_the_reported_failure(self) -> None:
        original = ValueError("Broken table")

        self.assertIsNone(classify_error(original))
        classified = classify_error(original, parsing=True)
        self.assertIsInstance(classified, ParseError)
        self.assertIs(classified.__cause__, original)
