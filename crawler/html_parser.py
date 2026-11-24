from bs4 import BeautifulSoup


class HTMLParser:
    async def parse_html(self, html: str, url: str) -> dict[str, str | dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        metadata = self.extract_metadata(soup)
        return {
            "url": url,
            "title": metadata["title"],
            "text": self.extract_text(soup),
            "metadata": metadata,
        }

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
