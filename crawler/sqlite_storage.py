import asyncio
from pathlib import Path
from typing import cast

import aiofiles.os
import aiosqlite

from .page_record import PageRecord, serialize_table_row
from .storage import DataStorage, StorageRecoveryError


_CREATE_PAGES = """
CREATE TABLE IF NOT EXISTS pages (
    url TEXT NOT NULL PRIMARY KEY,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    links TEXT NOT NULL,
    images TEXT NOT NULL,
    headings TEXT NOT NULL,
    lists TEXT NOT NULL,
    tables TEXT NOT NULL,
    metadata TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    crawled_at TEXT NOT NULL
)
"""

_UPSERT_PAGE = """
INSERT INTO pages (
    url, title, text, links, images, headings, lists, tables, metadata,
    status_code, content_type, crawled_at
) VALUES (
    :url, :title, :text, :links, :images, :headings, :lists, :tables, :metadata,
    :status_code, :content_type, :crawled_at
)
ON CONFLICT(url) DO UPDATE SET
    title = excluded.title,
    text = excluded.text,
    links = excluded.links,
    images = excluded.images,
    headings = excluded.headings,
    lists = excluded.lists,
    tables = excluded.tables,
    metadata = excluded.metadata,
    status_code = excluded.status_code,
    content_type = excluded.content_type,
    crawled_at = excluded.crawled_at
"""


class SQLiteStorage(DataStorage):
    """Store the latest saved record for each URL in a SQLite database."""

    def __init__(self, path: str | Path, *, batch_size: int = 1) -> None:
        if not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")

        self._path = Path(path)
        self._batch_size = batch_size
        self._pending_rows: list[dict[str, object]] = []
        self._failed_batch: BaseException | None = None
        self._connection_error: StorageRecoveryError | None = None
        self._connection: aiosqlite.Connection | None = None
        self._closed = False
        self._lock = asyncio.Lock()

    async def init_db(self) -> None:
        """Open the database and create the pages table if necessary."""
        async with self._lock:
            await self._get_connection()

    async def save(self, data: PageRecord) -> None:
        row = serialize_table_row(data)
        async with self._lock:
            connection = await self._get_connection()
            if len(self._pending_rows) + 1 < self._batch_size:
                self._pending_rows.append(row)
                return

            await self._write_batch(connection, [*self._pending_rows, row])
            self._pending_rows.clear()

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                if self._pending_rows:
                    if self._connection_error is not None:
                        self._failed_batch = self._connection_error
                        raise self._connection_error
                    await self._write_batch(self._connection, self._pending_rows)
                    self._pending_rows.clear()
                await self._connection.close()
                self._connection = None
            self._closed = True

    async def discard_failed_batch(self, error: Exception) -> tuple[str, ...]:
        async with self._lock:
            if self._failed_batch is not error:
                return ()
            urls = tuple(cast(str, row["url"]) for row in self._pending_rows)
            self._pending_rows.clear()
            self._failed_batch = None
            return urls

    async def _write_batch(
        self, connection: aiosqlite.Connection, rows: list[dict[str, object]]
    ) -> None:
        try:
            async with connection.executemany(_UPSERT_PAGE, rows):
                pass
            await connection.commit()
        except BaseException as error:
            try:
                await connection.rollback()
            except Exception as rollback_error:
                failure = StorageRecoveryError(f"Cannot roll back SQLite write: {rollback_error}")
                self._connection_error = failure
                self._failed_batch = failure
                raise failure from error
            self._failed_batch = error
            raise
        self._failed_batch = None

    async def _get_connection(self) -> aiosqlite.Connection:
        if self._closed:
            raise RuntimeError("SQLiteStorage is closed")
        if self._connection_error is not None:
            raise self._connection_error
        if self._connection is None:
            await aiofiles.os.makedirs(self._path.parent, exist_ok=True)
            connection = await aiosqlite.connect(self._path)
            try:
                async with connection.execute(_CREATE_PAGES):
                    pass
                await connection.commit()
            except BaseException:
                await connection.close()
                raise
            self._connection = connection
        return self._connection
