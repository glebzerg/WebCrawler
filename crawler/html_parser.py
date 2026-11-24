from bs4 import BeautifulSoup


class HTMLParser:
    async def parse_html(self, html: str, url: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "lxml")
        title_tag = soup.title
        title = title_tag.get_text(strip=True) if title_tag is not None else ""
        return {"url": url, "title": title}
