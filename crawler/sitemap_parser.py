import asyncio
from collections.abc import Awaitable, Callable

from lxml import etree

from .errors import ParseError
from .urls import resolve_http_url


_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"


class SitemapParser:
    def __init__(self, fetch_text: Callable[[str], Awaitable[str]]) -> None:
        self._fetch_text = fetch_text

    async def fetch_sitemap(self, sitemap_url: str) -> list[str]:
        """Download a sitemap and return the absolute HTTP(S) page URLs it lists."""
        xml = await self._fetch_text(sitemap_url)
        return await asyncio.to_thread(self._parse_urlset, xml, sitemap_url)

    def _parse_urlset(self, xml: str, sitemap_url: str) -> list[str]:
        parser = etree.XMLParser(
            encoding="utf-8", resolve_entities=False, load_dtd=False, no_network=True
        )
        try:
            root = etree.fromstring(xml.encode("utf-8"), parser)
        except etree.XMLSyntaxError as error:
            raise ParseError(f"Invalid sitemap XML at {sitemap_url}") from error

        if root.tag != f"{{{_NAMESPACE}}}urlset":
            raise ParseError(f"Expected a sitemap urlset at {sitemap_url}")

        urls = []
        for location in root.findall("sm:url/sm:loc", {"sm": _NAMESPACE}):
            if location.text is None or len(location):
                continue
            # With no base URL, a relative location fails HTTP(S) validation.
            url = resolve_http_url("", location.text)
            if url is not None:
                urls.append(url)
        return urls
