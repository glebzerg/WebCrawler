import asyncio
import logging

from aiohttp import (
    ClientConnectionError,
    ClientConnectorError,
    ClientError,
    ClientSession,
    ClientTimeout,
)

from .html_parser import HTMLParser, ParsedPage

logger = logging.getLogger(__name__)


class FetchConnectionError(ClientConnectionError):
    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"Failed to connect to {url}")


class AsyncCrawler:
    def __init__(
        self,
        max_concurrent: int = 10,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
    ) -> None:
        if not isinstance(max_concurrent, int) or max_concurrent < 1:
            raise ValueError("max_concurrent must be a positive integer")

        self._request_slots = asyncio.Semaphore(max_concurrent)
        self._timeout = ClientTimeout(
            total=5 * 60,
            connect=connect_timeout,
            sock_read=read_timeout,
        )
        self._session: ClientSession | None = None
        self._closed = False
        self.failed_urls: dict[str, Exception] = {}

    def _get_session(self) -> ClientSession:
        if self._closed:
            raise RuntimeError("AsyncCrawler is closed")

        if self._session is None:
            self._session = ClientSession(timeout=self._timeout)
        return self._session

    async def fetch_url(self, url: str) -> str:
        text, _ = await self._fetch_page(url)
        return text

    async def fetch_and_parse(self, url: str) -> ParsedPage:
        html, final_url = await self._fetch_page(url)
        page = await HTMLParser().parse_html(html, final_url)
        page["url"] = url
        return page

    async def _fetch_page(self, url: str) -> tuple[str, str]:
        async with self._request_slots:
            session = self._get_session()
            logger.info(
                "Started fetching %s",
                url,
                extra={"event": "fetch_started", "url": url},
            )
            try:
                async with session.get(url) as response:
                    response.raise_for_status()
                    text = await response.text()
                    final_url = str(response.url)
            except Exception as error:
                error_type = type(error).__name__
                logger.warning(
                    "Failed to fetch %s (%s)",
                    url,
                    error_type,
                    extra={"event": "fetch_failed", "url": url, "error_type": error_type},
                )
                if isinstance(error, ClientConnectorError):
                    raise FetchConnectionError(url) from error
                raise

            logger.info(
                "Finished fetching %s",
                url,
                extra={"event": "fetch_succeeded", "url": url},
            )
            return text, final_url

    async def fetch_urls(self, urls: list[str]) -> dict[str, str]:
        """Fetch each unique URL and record failures for the completed batch."""
        batch_urls = list(dict.fromkeys(urls))
        outcomes = await asyncio.gather(
            *(self.fetch_url(url) for url in batch_urls),
            return_exceptions=True,
        )

        pages: dict[str, str] = {}
        failures: dict[str, Exception] = {}
        for url, outcome in zip(batch_urls, outcomes, strict=True):
            if isinstance(outcome, (ClientError, TimeoutError, UnicodeDecodeError)):
                failures[url] = outcome
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                pages[url] = outcome

        self.failed_urls = failures
        return pages

    async def close(self) -> None:
        self._closed = True
        if self._session is not None:
            await self._session.close()
