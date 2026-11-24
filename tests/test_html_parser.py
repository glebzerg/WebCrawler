import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from crawler import HTMLParser


PAGE_FIXTURE = Path(__file__).with_name("fixtures") / "page.html"


class HTMLParserTest(unittest.IsolatedAsyncioTestCase):
    async def test_parse_html_reads_metadata_from_fixture(self) -> None:
        html = PAGE_FIXTURE.read_text(encoding="utf-8")
        url = "https://example.com/fixture"

        page = await HTMLParser().parse_html(html, url)

        self.assertEqual(
            page,
            {
                "url": url,
                "title": "Учебная страница",
                "text": "Меню сайта Добро пожаловать Этот HTML используется в тестах парсера.",
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
                "text": "",
                "metadata": {"title": "", "description": "", "keywords": "python"},
            },
        )

    def test_extract_text_returns_only_the_selected_article(self) -> None:
        soup = BeautifulSoup(PAGE_FIXTURE.read_text(encoding="utf-8"), "lxml")

        self.assertEqual(
            HTMLParser().extract_text(soup, selector="#main"),
            "Добро пожаловать Этот HTML используется в тестах парсера.",
        )

    def test_extract_text_returns_empty_for_missing_or_excluded_elements(self) -> None:
        soup = BeautifulSoup(PAGE_FIXTURE.read_text(encoding="utf-8"), "lxml")
        parser = HTMLParser()

        for selector in ("#missing", "script", "style"):
            with self.subTest(selector=selector):
                self.assertEqual(parser.extract_text(soup, selector=selector), "")
