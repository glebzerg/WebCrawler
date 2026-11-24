from bs4 import BeautifulSoup


class HTMLParser:
    async def parse_html(self, html: str, url: str) -> dict[str, str | dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        metadata = self.extract_metadata(soup)
        return {"url": url, "title": metadata["title"], "metadata": metadata}

    def extract_metadata(self, soup: BeautifulSoup) -> dict[str, str]:
        title_tag = soup.title
        title = title_tag.get_text(strip=True) if title_tag is not None else ""
        metadata = {"title": title}

        for name in ("description", "keywords"):
            tag = soup.select_one(f'meta[name="{name}" i]')
            content = tag.get("content") if tag is not None else None
            metadata[name] = content.strip() if isinstance(content, str) else ""

        return metadata
