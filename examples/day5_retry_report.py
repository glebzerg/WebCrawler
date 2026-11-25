import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path

import aiofiles
import aiofiles.os
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler import AsyncCrawler, RetryStrategy


OUTPUT = Path("results") / "day5-retry-report.json"
SCENARIOS = {"timeout": "/slow", "recovered": "/recover", "not_found": "/missing"}
JOURNAL_FIELDS = (
    "event",
    "attempt",
    "status_code",
    "error_type",
    "error_category",
    "retry_delay",
)


class CrawlJournal(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.by_url: defaultdict[str, list[dict[str, object]]] = defaultdict(list)

    def emit(self, record: logging.LogRecord) -> None:
        fields = vars(record)
        if "url" in fields and "event" in fields and "attempt" in fields:
            entry = {key: fields[key] for key in JOURNAL_FIELDS if key in fields}
            self.by_url[fields["url"]].append(entry)


async def main() -> None:
    release_slow = asyncio.Event()
    recover_attempts = 0

    async def slow_page(_request: web.Request) -> web.Response:
        await release_slow.wait()
        return web.Response(text="Late response")

    async def recover_page(_request: web.Request) -> web.Response:
        nonlocal recover_attempts
        recover_attempts += 1
        if recover_attempts == 1:
            return web.Response(status=503, headers={"Retry-After": "1"})
        return web.Response(text="<h1>Recovered</h1>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/slow", slow_page)
    app.router.add_get("/recover", recover_page)

    journal = CrawlJournal()
    logger = logging.getLogger("crawler.core")
    previous_level = logger.level
    previous_propagate = logger.propagate

    async with TestServer(app, host="127.0.0.1", port=0) as server:
        urls = {name: str(server.make_url(path)) for name, path in SCENARIOS.items()}
        crawler = AsyncCrawler(
            max_concurrent=3,
            read_timeout=0.5,
            retry_strategy=RetryStrategy(max_retries=1, initial_delay=0.1),
        )
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(journal)
        try:
            pages = await crawler.crawl(list(urls.values()), max_depth=0)
        finally:
            logger.removeHandler(journal)
            journal.close()
            logger.propagate = previous_propagate
            logger.setLevel(previous_level)
            release_slow.set()
            await crawler.close()

    scenarios = {}
    for name, url in urls.items():
        events = journal.by_url[url]
        attempts = sum(event["event"] == "fetch_started" for event in events)
        error = crawler.failed_urls.get(url)
        outcome = "succeeded" if url in pages else "failed"
        scenarios[name] = {
            "url": url,
            "outcome": outcome,
            "attempts": attempts,
            "final_error": str(error) if error is not None else None,
            "journal": events,
        }
        print(f"{name}: {outcome}, {attempts} HTTP attempt(s)")

    report = {"stats": crawler.get_stats(), "scenarios": scenarios}
    await aiofiles.os.makedirs(OUTPUT.parent, exist_ok=True)
    async with aiofiles.open(OUTPUT, "w", encoding="utf-8") as file:
        await file.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Saved: {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
