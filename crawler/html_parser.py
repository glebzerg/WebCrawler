from typing import TypedDict
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag


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


def _resolve_http_url(base_url: str, reference: str) -> str | None:
    reference = reference.strip()
    if not reference:
        return None

    try:
        source = urlsplit(reference)
        if (source.scheme or reference.startswith("//")) and not source.hostname:
            return None

        url = urljoin(base_url, reference)
        address = urlsplit(url)
        host = address.hostname
        if address.scheme not in {"http", "https"} or not host:
            return None
        if any(character.isspace() for character in host):
            return None

        # Accessing port validates its format and range.
        _ = address.port
    except ValueError:
        return None

    return url


def _document_base_url(soup: BeautifulSoup, page_url: str) -> str:
    base = soup.select_one("base[href]")
    href = base.get("href") if base is not None else None
    if isinstance(href, str):
        return _resolve_http_url(page_url, href) or page_url
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


class HTMLParser:
    async def parse_html(self, html: str, url: str) -> ParsedPage:
        soup = BeautifulSoup(html, "lxml")
        metadata = self.extract_metadata(soup)
        return {
            "url": url,
            "title": metadata["title"],
            "text": self.extract_text(soup),
            "links": self.extract_links(soup, url),
            "images": self.extract_images(soup, url),
            "headings": self.extract_headings(soup),
            "lists": self.extract_lists(soup),
            "tables": self.extract_tables(soup),
            "metadata": metadata,
        }

    def extract_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        base_url = _document_base_url(soup, base_url)
        links = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            if isinstance(href, str):
                url = _resolve_http_url(base_url, href)
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
            url = _resolve_http_url(base_url, source)
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
