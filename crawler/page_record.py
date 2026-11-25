import json
from datetime import UTC, datetime

from .html_parser import ParsedPage


class PageRecord(ParsedPage):
    status_code: int
    content_type: str
    crawled_at: datetime


def serialize_page(page: PageRecord) -> dict[str, object]:
    """Return a copy with the timestamp formatted as ISO 8601 in UTC."""
    crawled_at = page["crawled_at"]
    if crawled_at.utcoffset() is None:
        raise ValueError("crawled_at must include a timezone")
    return {**page, "crawled_at": crawled_at.astimezone(UTC).isoformat()}


def serialize_table_row(page: PageRecord) -> dict[str, object]:
    """Serialize nested fields as JSON for storage in table cells."""
    row = serialize_page(page)
    for name, value in row.items():
        if isinstance(value, (dict, list)):
            row[name] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return row
