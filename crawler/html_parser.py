import asyncio
import logging
from collections.abc import Callable
from typing import TypedDict

from bs4 import BeautifulSoup, Tag
from bs4.exceptions import ParserRejectedMarkup

from .errors import ParseError
from .urls import resolve_http_url


logger = logging.getLogger(__name__)


class ParsedPage(TypedDict):
    url: str
    title: str
    text: str
    links: list[str]
    images: list[dict[str, str]]
    headings: list[dict[str, int | str]]
    lists: list[dict[str, str | list[str]]]
    tables: list[list[list[str]]]
    metadata: dict[str, str]


def _document_base_url(soup: BeautifulSoup, page_url: str) -> str:
    base = soup.select_one("base[href]")
    href = base.get("href") if base is not None else None
    if isinstance(href, str):
        return resolve_http_url(page_url, href) or page_url
    return page_url


def _text_in_container(
    element: Tag, container: Tag, container_tags: tuple[str, ...]
) -> str:
    parts = (
        text.strip()
        for text in element.strings
        if text.find_parent(container_tags) is container
    )
    return " ".join(filter(None, parts))


def _log_parse_warning(url: str, field: str, error: Exception) -> None:
    error_type = type(error).__name__
    logger.warning(
        "Failed to parse %s for %s (%s)",
        field,
        url,
        error_type,
        extra={
            "event": "html_parse_failed",
            "url": url,
            "field": field,
            "error_type": error_type,
            "error_category": ParseError.__name__,
        },
    )


def _extract_field[T](url: str, field: str, extract: Callable[[], T], default: T) -> T:
    try:
        return extract()
    except Exception as error:
        _log_parse_warning(url, field, error)
        return default


class HTMLParser:
    async def parse_html(self, html: str, url: str) -> ParsedPage:
        return await asyncio.to_thread(self._parse_html, html, url)

    def _parse_html(self, html: str, url: str) -> ParsedPage:
        try:
            soup = BeautifulSoup(html, "lxml")
        except ParserRejectedMarkup as error:
            _log_parse_warning(url, "html", error)
            soup = BeautifulSoup("", "lxml")

        metadata = _extract_field(
            url,
            "metadata",
            lambda: self.extract_metadata(soup),
            {"title": "", "description": "", "keywords": ""},
        )
        return {
            "url": url,
            "title": metadata["title"],
            "text": _extract_field(url, "text", lambda: self.extract_text(soup), ""),
            "links": _extract_field(url, "links", lambda: self.extract_links(soup, url), []),
            "images": _extract_field(url, "images", lambda: self.extract_images(soup, url), []),
            "headings": _extract_field(url, "headings", lambda: self.extract_headings(soup), []),
            "lists": _extract_field(url, "lists", lambda: self.extract_lists(soup), []),
            "tables": _extract_field(url, "tables", lambda: self.extract_tables(soup), []),
            "metadata": metadata,
        }

    def extract_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        base_url = _document_base_url(soup, base_url)
        links = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            if isinstance(href, str):
                url = resolve_http_url(base_url, href)
                if url is not None:
                    links.append(url)
        return links

    def extract_images(self, soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
        base_url = _document_base_url(soup, base_url)
        images = []
        for image in soup.select("img[src]"):
            source = image.get("src")
            if not isinstance(source, str):
                continue
            url = resolve_http_url(base_url, source)
            if url is None:
                continue

            alt = image.get("alt")
            images.append({"src": url, "alt": alt if isinstance(alt, str) else ""})
        return images

    def extract_headings(self, soup: BeautifulSoup) -> list[dict[str, int | str]]:
        return [
            {"level": int(heading.name[1]), "text": heading.get_text(" ", strip=True)}
            for heading in soup.select("h1, h2, h3")
        ]

    def extract_lists(self, soup: BeautifulSoup) -> list[dict[str, str | list[str]]]:
        lists = []
        for list_tag in soup.select("ul, ol"):
            items = [
                _text_in_container(item, list_tag, ("ul", "ol"))
                for item in list_tag.find_all("li", recursive=False)
            ]
            lists.append({"type": list_tag.name, "items": items})
        return lists

    def extract_tables(self, soup: BeautifulSoup) -> list[list[list[str]]]:
        tables = []
        for table in soup.find_all("table"):
            rows = []
            for row in table.find_all("tr"):
                if row.find_parent("table") is not table:
                    continue
                cells = [
                    _text_in_container(cell, table, ("table",))
                    for cell in row.find_all(("th", "td"), recursive=False)
                ]
                rows.append(cells)
            tables.append(rows)
        return tables

    def extract_text(self, soup: BeautifulSoup, selector: str | None = None) -> str:
        section = soup.body if selector is None else soup.select_one(selector)
        if section is None or section.name in {"script", "style"}:
            return ""
        return section.get_text(separator=" ", strip=True)

    def extract_metadata(self, soup: BeautifulSoup) -> dict[str, str]:
        title_tag = soup.title
        title = title_tag.get_text(strip=True) if title_tag is not None else ""
        metadata = {"title": title}

        for name in ("description", "keywords"):
            tag = soup.select_one(f'meta[name="{name}" i]')
            content = tag.get("content") if tag is not None else None
            metadata[name] = content.strip() if isinstance(content, str) else ""

        return metadata
