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
                "links": [],
                "images": [],
                "headings": [{"level": 1, "text": "Добро пожаловать"}],
                "lists": [],
                "tables": [],
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
                "links": [],
                "images": [],
                "headings": [],
                "lists": [],
                "tables": [],
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

    async def test_parse_html_resolves_links_using_the_html_base(self) -> None:
        html = PAGE_FIXTURE.with_name("links.html").read_text(encoding="utf-8")
        url = "https://example.com/blog/posts/current.html"

        page = await HTMLParser().parse_html(html, url)

        self.assertEqual(
            page["links"],
            [
                "https://example.com/blog/guide/intro.html",
                "https://example.com/blog/index.html",
                "https://docs.example.org/start",
                "https://cdn.example.org/help",
            ],
        )

    def test_extract_links_falls_back_to_page_url_for_missing_or_invalid_base(self) -> None:
        parser = HTMLParser()
        page_url = "https://example.com/blog/posts/current.html"

        for base in ("", '<base href="mailto:hello@example.com">'):
            with self.subTest(base=base):
                html = f'<head>{base}</head><body><a href="../guide.html">Guide</a></body>'
                soup = BeautifulSoup(html, "lxml")
                self.assertEqual(
                    parser.extract_links(soup, page_url),
                    ["https://example.com/blog/guide.html"],
                )

    async def test_parse_html_extracts_image_urls_and_alt_text(self) -> None:
        html = """
            <head><base href="../media/"></head>
            <body>
                <img src="cover.png" alt="Обложка">
                <img src="//cdn.example.org/logo.svg">
                <img alt="Изображение без адреса">
                <img src="">
                <img src="data:image/png;base64,AAAA">
            </body>
        """
        url = "https://example.com/blog/posts/current.html"

        page = await HTMLParser().parse_html(html, url)

        self.assertEqual(
            page["images"],
            [
                {"src": "https://example.com/blog/media/cover.png", "alt": "Обложка"},
                {"src": "https://cdn.example.org/logo.svg", "alt": ""},
            ],
        )

    async def test_parse_html_keeps_heading_levels_and_list_order(self) -> None:
        html = """
            <h2>Подготовка</h2>
            <ul>
                <li>Открыть <strong>терминал</strong></li>
                <li>Установить пакеты
                    <ol><li>aiohttp</li><li>lxml</li></ol>
                    и проверить импорты
                </li>
            </ul>
            <h1>Запуск</h1>
            <h3>Порядок действий</h3>
            <ol><li>Запустить пример</li><li>Прочитать результат</li></ol>
            <h4>Дополнение</h4>
        """

        page = await HTMLParser().parse_html(html, "https://example.com/structure")

        self.assertEqual(
            page["headings"],
            [
                {"level": 2, "text": "Подготовка"},
                {"level": 1, "text": "Запуск"},
                {"level": 3, "text": "Порядок действий"},
            ],
        )
        self.assertEqual(
            page["lists"],
            [
                {
                    "type": "ul",
                    "items": ["Открыть терминал", "Установить пакеты и проверить импорты"],
                },
                {"type": "ol", "items": ["aiohttp", "lxml"]},
                {"type": "ol", "items": ["Запустить пример", "Прочитать результат"]},
            ],
        )

    async def test_parse_html_preserves_table_rows_and_empty_cells(self) -> None:
        html = """
            <table>
                <thead><tr><th>Пакет</th><th>Версия</th><th>Назначение</th></tr></thead>
                <tbody>
                    <tr><th>aiohttp</th><td> </td><td>HTTP-клиент</td></tr>
                    <tr><th>lxml</th><td>6</td><td>Разбор <strong>HTML</strong></td></tr>
                </tbody>
            </table>
        """

        page = await HTMLParser().parse_html(html, "https://example.com/packages")

        self.assertEqual(
            page["tables"],
            [
                [
                    ["Пакет", "Версия", "Назначение"],
                    ["aiohttp", "", "HTTP-клиент"],
                    ["lxml", "6", "Разбор HTML"],
                ],
            ],
        )

    def test_extract_tables_keeps_nested_table_separate(self) -> None:
        html = """
            <table><tr>
                <td>До <table><tr><td>Внутри</td></tr></table> после</td>
                <td>Соседняя ячейка</td>
            </tr></table>
        """
        soup = BeautifulSoup(html, "lxml")

        self.assertEqual(
            HTMLParser().extract_tables(soup),
            [[["До после", "Соседняя ячейка"]], [["Внутри"]]],
        )
