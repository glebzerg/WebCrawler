import asyncio
import csv
import json
from abc import ABC, abstractmethod
from io import StringIO
from pathlib import Path
from textwrap import indent

import aiofiles
import aiofiles.os
from aiofiles.threadpool.text import AsyncTextIOWrapper

from .page_record import PageRecord, serialize_page, serialize_table_row


class DataStorage(ABC):
    """Asynchronous storage for records produced by a crawl."""

    @abstractmethod
    async def save(self, data: PageRecord) -> None:
        """Save one page record."""

    @abstractmethod
    async def close(self) -> None:
        """Finish writing and release any resources held by the storage."""

    async def discard_failed_batch(self, error: Exception) -> tuple[str, ...]:
        """Discard previously accepted records in a batch that cannot be saved."""
        return ()


class StorageRecoveryError(RuntimeError):
    """The failed storage operation cannot safely be repeated."""


class _TextFileStorage(DataStorage):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._encoding = "utf-8"
        self._file: AsyncTextIOWrapper | None = None
        self._closed = False
        self._write_failed = False
        self._lock = asyncio.Lock()

    async def _get_file(self) -> AsyncTextIOWrapper:
        if self._file is None:
            await aiofiles.os.makedirs(self._path.parent, exist_ok=True)
            self._file = await aiofiles.open(self._path, "w", encoding=self._encoding, newline="")
        return self._file

    async def _write_chunk(self, chunk: str) -> None:
        if self._write_failed:
            raise StorageRecoveryError(f"Cannot write {self._path} after a failed rollback")

        file = await self._get_file()
        position = await file.tell()
        try:
            if await file.write(chunk) != len(chunk):
                raise OSError("Incomplete file write")
            await file.flush()
        except Exception as error:
            try:
                await file.seek(position)
                await file.truncate()
            except Exception as rollback_error:
                self._write_failed = True
                raise StorageRecoveryError(
                    f"Cannot restore {self._path} after {error}: {rollback_error}"
                ) from rollback_error
            raise

    async def _close_file(self, footer: str = "") -> None:
        file = await self._get_file()
        self._closed = True
        try:
            try:
                if self._write_failed:
                    raise OSError("File position could not be restored")
                if footer:
                    await self._write_chunk(footer)
            finally:
                await file.close()
        except Exception as error:
            raise StorageRecoveryError(f"Cannot finalize {self._path}: {error}") from error


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
            prefix = ",\n" if self._has_records else "[\n"
            await self._write_chunk(prefix + indent(record, "  "))
            self._has_records = True

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return

            await self._close_file("\n]\n" if self._has_records else "[]\n")


class JSONLStorage(_TextFileStorage):
    """Write one UTF-8 JSON object per line."""

    async def save(self, data: PageRecord) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("JSONLStorage is closed")

            record = json.dumps(serialize_page(data), ensure_ascii=False, separators=(",", ":"))
            await self._write_chunk(record + "\n")

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return

            await self._close_file()


class CSVStorage(_TextFileStorage):
    """Write page records as CSV, with JSON in nested fields."""

    _fieldnames = tuple(PageRecord.__annotations__)

    def __init__(self, path: str | Path, *, encoding: str = "utf-8") -> None:
        super().__init__(path)
        self._encoding = encoding
        self._has_records = False

    async def save(self, data: PageRecord) -> None:
        async with self._lock:
            if self._closed:
                raise RuntimeError("CSVStorage is closed")

            chunk = self._format_csv(serialize_table_row(data))
            await self._write_chunk(chunk)
            self._has_records = True

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return

            await self._close_file("" if self._has_records else self._format_csv())

    def _format_csv(self, row: dict[str, object] | None = None) -> str:
        with StringIO(newline="") as buffer:
            writer = csv.DictWriter(buffer, fieldnames=self._fieldnames)
            if not self._has_records:
                writer.writeheader()
            if row is not None:
                writer.writerow(row)
            return buffer.getvalue()
