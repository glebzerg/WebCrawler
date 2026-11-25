from socket import gaierror

from aiohttp import (
    ClientConnectionError,
    ClientPayloadError,
    ClientResponseError,
    InvalidURL,
)
from bs4.exceptions import ParserRejectedMarkup


_TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}


class CrawlError(Exception):
    """Base class for classified crawler failures."""


class TransientError(CrawlError):
    """A timeout or temporary HTTP response."""


class PermanentError(CrawlError):
    """An HTTP response or URL error that should not be repeated."""


class NetworkError(CrawlError):
    """A problem connecting to the server or receiving its response."""


class ParseError(CrawlError):
    """A problem decoding or parsing content."""


def classify_error(error: Exception, *, parsing: bool = False) -> CrawlError | None:
    """Classify a known failure while retaining the original exception."""
    if isinstance(error, CrawlError):
        return error

    category: type[CrawlError]
    if parsing or isinstance(error, (ParserRejectedMarkup, UnicodeDecodeError)):
        category = ParseError
    elif isinstance(error, TimeoutError):
        category = TransientError
    elif isinstance(error, ClientResponseError):
        category = (
            TransientError if error.status in _TRANSIENT_HTTP_STATUSES else PermanentError
        )
    elif isinstance(
        error, (ClientConnectionError, ClientPayloadError, ConnectionError, gaierror)
    ):
        category = NetworkError
    elif isinstance(error, InvalidURL):
        category = PermanentError
    else:
        return None

    classified = category(str(error))
    classified.__cause__ = error
    return classified
