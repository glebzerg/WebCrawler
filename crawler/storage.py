from abc import ABC, abstractmethod

from .page_record import PageRecord


class DataStorage(ABC):
    """Asynchronous storage for records produced by a crawl."""

    @abstractmethod
    async def save(self, data: PageRecord) -> None:
        """Save one page record."""

    @abstractmethod
    async def close(self) -> None:
        """Finish writing and release any resources held by the storage."""
