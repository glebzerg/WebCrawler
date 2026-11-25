import asyncio
import csv
import json
from pathlib import Path
from tempfile import mkdtemp

import aiofiles
import aiofiles.os
import aiosqlite
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler import AsyncCrawler, CSVStorage, JSONStorage, SQLiteStorage, serialize_page


OUTPUT = Path("results") / "day6-storage"
PAGES = {
    "/": (
        "<title>Начало</title>"
        '<pre>Текст, "пример"\nВторая строка</pre>'
        '<a href="/guide">Руководство</a>'
        '<img src="/diagram.png" alt="Схема">'
    ),
    "/guide": (
        "<title>Руководство</title>"
        '<meta name="description" content="Краткое руководство">'
        "<ul><li>JSON</li><li>CSV</li><li>SQLite</li></ul>"
        '<a href="/">Начало</a><a href="/reference">Справка</a>'
    ),
    "/reference": (
        "<title>Справка</title>"
        "<table><tr><th>Формат</th></tr><tr><td>SQLite</td></tr></table>"
    ),
}
JSON_COLUMNS = ("links", "images", "headings", "lists", "tables", "metadata")


async def local_page(request: web.Request) -> web.Response:
    html = PAGES.get(request.path)
    if html is None:
        raise web.HTTPNotFound()
    return web.Response(text=html, content_type="text/html")


def decode_table_row(row: dict[str, object]) -> dict[str, object]:
    for column in JSON_COLUMNS:
        row[column] = json.loads(str(row[column]))
    row["status_code"] = int(str(row["status_code"]))
    return row


async def read_json(path: Path) -> list[dict[str, object]]:
    async with aiofiles.open(path, encoding="utf-8") as file:
        return json.loads(await file.read())


async def read_csv(path: Path) -> list[dict[str, object]]:
    def read_rows() -> list[dict[str, object]]:
        with path.open(encoding="utf-8", newline="") as file:
            return [decode_table_row(dict(row)) for row in csv.DictReader(file)]

    return await asyncio.to_thread(read_rows)


async def read_sqlite(path: Path) -> list[dict[str, object]]:
    async with aiosqlite.connect(path) as database:
        database.row_factory = aiosqlite.Row
        async with database.execute("SELECT * FROM pages") as cursor:
            return [decode_table_row(dict(row)) async for row in cursor]


async def main() -> None:
    await aiofiles.os.makedirs(OUTPUT, exist_ok=True)
    directory = Path(await asyncio.to_thread(mkdtemp, dir=OUTPUT, prefix="run-"))
    json_path = directory / "pages.json"
    csv_path = directory / "pages.csv"
    sqlite_path = directory / "pages.db"
    formats = (
        ("JSON", json_path, JSONStorage(json_path), read_json),
        ("CSV", csv_path, CSVStorage(csv_path), read_csv),
        ("SQLite", sqlite_path, SQLiteStorage(sqlite_path, batch_size=2), read_sqlite),
    )

    app = web.Application()
    app.router.add_get("/{path:.*}", local_page)
    summary = {}
    baseline = None
    async with TestServer(app, host="127.0.0.1", port=0) as server:
        start = str(server.make_url("/"))
        for name, path, storage, read_pages in formats:
            crawler = AsyncCrawler(storage=storage)
            try:
                pages = await crawler.crawl([start], same_domain_only=True)
            finally:
                await crawler.close()

            stats = crawler.get_stats()
            if crawler.failed_urls or stats["storage_errors"] or stats["storage_close_error"]:
                raise RuntimeError(f"{name} crawl or storage failed")

            rows = await read_pages(path)
            read_by_url = {str(row["url"]): row for row in rows}
            expected = {url: serialize_page(page) for url, page in pages.items()}
            if len(rows) != stats["saved_pages"] or len(rows) != len(PAGES) or read_by_url != expected:
                raise RuntimeError(f"{name} records differ from the crawled pages")

            comparable = {
                url: {key: value for key, value in row.items() if key != "crawled_at"}
                for url, row in read_by_url.items()
            }
            if baseline is None:
                baseline = comparable
            elif comparable != baseline:
                raise RuntimeError(f"{name} records differ from the JSON records")

            summary[name] = {
                "file": path.name,
                "saved_pages": stats["saved_pages"],
                "read_pages": len(rows),
            }
            print(f"{name}: {stats['saved_pages']} saved, {len(rows)} read")

    report = {"matches": True, "formats": summary, "urls": sorted(baseline or {})}
    report_path = directory / "report.json"
    async with aiofiles.open(report_path, "w", encoding="utf-8") as file:
        await file.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
