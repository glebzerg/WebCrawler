import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from aiohttp import ClientError
from lxml import etree

from .errors import ParseError
from .urls import resolve_http_url, url_key


_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"


class SitemapParser:
    def __init__(
        self,
        fetch_text: Callable[[str], Awaitable[str]],
        *,
        allow_sitemap: Callable[[str], bool] | None = None,
    ) -> None:
        self._fetch_text = fetch_text
        self._allow_sitemap = allow_sitemap

    async def fetch_sitemap(self, sitemap_url: str) -> list[str]:
        """Return page URLs from a sitemap or an index and its child sitemaps."""
        return [url async for url in self.iter_page_urls((sitemap_url,))]

    async def iter_page_urls(
        self,
        sitemap_urls: Sequence[str],
        *,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> AsyncIterator[str]:
        """Visit each map URL once and optionally report and skip failed branches."""
        pending = list(reversed(sitemap_urls))
        visited: set[str] = set()
        while pending:
            sitemap_url = pending.pop()
            try:
                if self._allow_sitemap is not None and not self._allow_sitemap(sitemap_url):
                    continue
                key = url_key(sitemap_url)
                if key in visited:
                    continue
                visited.add(key)

                xml = await self._fetch_text(sitemap_url)
                is_index, urls = await asyncio.to_thread(self._parse_document, xml, sitemap_url)
            except (ClientError, TimeoutError, ValueError, ParseError) as error:
                if on_error is None:
                    raise
                on_error(sitemap_url, error)
                continue

            if is_index:
                pending.extend(reversed(urls))
            else:
                for url in urls:
                    yield url

    def _parse_document(self, xml: str, sitemap_url: str) -> tuple[bool, list[str]]:
        parser = etree.XMLParser(
            encoding="utf-8", resolve_entities=False, load_dtd=False, no_network=True
        )
        try:
            root = etree.fromstring(xml.encode("utf-8"), parser)
        except etree.XMLSyntaxError as error:
            raise ParseError(f"Invalid sitemap XML at {sitemap_url}") from error

        is_index = root.tag == f"{{{_NAMESPACE}}}sitemapindex"
        if not is_index and root.tag != f"{{{_NAMESPACE}}}urlset":
            raise ParseError(f"Expected a sitemap urlset or sitemapindex at {sitemap_url}")

        urls = []
        path = "sm:sitemap/sm:loc" if is_index else "sm:url/sm:loc"
        for location in root.findall(path, {"sm": _NAMESPACE}):
            if location.text is None or len(location):
                continue
            # With no base URL, a relative location fails HTTP(S) validation.
            url = resolve_http_url("", location.text)
            if url is not None:
                urls.append(url)
        return is_index, urls
