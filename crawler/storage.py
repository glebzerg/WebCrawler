import asyncio
import json
from abc import ABC, abstractmethod
from pathlib import Path
from textwrap import indent

import aiofiles
import aiofiles.os
from aiofiles.threadpool.text import AsyncTextIOWrapper

from .page_record import PageRecord, serialize_page


class DataStorage(ABC):
    """Asynchronous storage for records produced by a crawl."""

    @abstractmethod
    async def save(self, data: PageRecord) -> None:
        """Save one page record."""

    @abstractmethod
    async def close(self) -> None:
        """Finish writing and release any resources held by the storage."""


class _TextFileStorage(DataStorage):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._file: AsyncTextIOWrapper | None = None
        self._closed = False
        self._lock = asyncio.Lock()

    async def _get_file(self) -> AsyncTextIOWrapper:
        if self._file is None:
            await aiofiles.os.makedirs(self._path.parent, exist_ok=True)
            self._file = await aiofiles.open(self._path, "w", encoding="utf-8", newline="\n")
        return self._file


class JSONStorage(_TextFileStorage):
    """Write a UTF-8 JSON array that is finalized on close."""

    def __init__(self, path: str | Path) -> None:
        super().__init__(path)
        self._has_records = False

    async def save(self, data: PageRecord) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("JSONStorage is closed")

            record = json.dumps(serialize_page(data), ensure_ascii=False, indent=2)
            file = await self._get_file()
            prefix = ",\n" if self._has_records else "[\n"
            await file.write(prefix + indent(record, "  "))
            self._has_records = True

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return

            file = await self._get_file()
            self._closed = True
            try:
                await file.write("\n]\n" if self._has_records else "[]\n")
            finally:
                await file.close()


class JSONLStorage(_TextFileStorage):
    """Write one UTF-8 JSON object per line."""

    async def save(self, data: PageRecord) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("JSONLStorage is closed")

            record = json.dumps(serialize_page(data), ensure_ascii=False, separators=(",", ":"))
            file = await self._get_file()
            await file.write(record + "\n")

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return

            file = await self._get_file()
            self._closed = True
            await file.close()
