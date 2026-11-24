from aiohttp import ClientConnectionError, ClientConnectorError, ClientSession


class FetchConnectionError(ClientConnectionError):
    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"Failed to connect to {url}")


class AsyncCrawler:
    def __init__(self) -> None:
        self._session: ClientSession | None = None
        self._closed = False

    def _get_session(self) -> ClientSession:
        if self._closed:
            raise RuntimeError("AsyncCrawler is closed")

        if self._session is None:
            self._session = ClientSession()
        return self._session

    async def fetch_url(self, url: str) -> str:
        session = self._get_session()
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.text()
        except ClientConnectorError as error:
            raise FetchConnectionError(url) from error

    async def close(self) -> None:
        self._closed = True
        if self._session is not None:
            await self._session.close()
