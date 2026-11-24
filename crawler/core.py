from aiohttp import ClientSession


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
        async with session.get(url) as response:
            return await response.text()

    async def close(self) -> None:
        self._closed = True
        if self._session is not None:
            await self._session.close()
