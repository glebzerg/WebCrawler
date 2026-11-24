import unittest
from pathlib import Path

from crawler import HTMLParser


class HTMLParserTest(unittest.IsolatedAsyncioTestCase):
    async def test_parse_html_reads_metadata_from_fixture(self) -> None:
        fixture = Path(__file__).with_name("fixtures") / "page.html"
        html = fixture.read_text(encoding="utf-8")
        url = "https://example.com/fixture"

        page = await HTMLParser().parse_html(html, url)

        self.assertEqual(
            page,
            {
                "url": url,
                "title": "Учебная страница",
                "metadata": {
                    "title": "Учебная страница",
                    "description": "Пример для краулера",
                    "keywords": "python, html",
                },
            },
        )

    async def test_parse_html_uses_empty_strings_for_missing_metadata(self) -> None:
        html = '<meta name="keywords" content="python">'
        url = "https://example.com/partial"

        page = await HTMLParser().parse_html(html, url)

        self.assertEqual(
            page,
            {
                "url": url,
                "title": "",
                "metadata": {"title": "", "description": "", "keywords": "python"},
            },
        )
