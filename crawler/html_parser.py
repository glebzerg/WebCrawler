from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


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


class HTMLParser:
    async def parse_html(
        self, html: str, url: str
    ) -> dict[str, str | list[str] | dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        metadata = self.extract_metadata(soup)
        return {
            "url": url,
            "title": metadata["title"],
            "text": self.extract_text(soup),
            "links": self.extract_links(soup, url),
            "metadata": metadata,
        }

    def extract_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        base = soup.select_one("base[href]")
        if base is not None:
            href = base.get("href")
            if isinstance(href, str):
                base_url = _resolve_http_url(base_url, href) or base_url

        links = []
        for anchor in soup.select("a[href]"):
            href = anchor.get("href")
            if isinstance(href, str):
                url = _resolve_http_url(base_url, href)
                if url is not None:
                    links.append(url)
        return links

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
