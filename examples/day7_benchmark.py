import argparse
import asyncio
import gc
import hashlib
import json
import os
import platform
import sys
import tracemalloc
from datetime import UTC, datetime
from http.client import HTTPConnection
from pathlib import Path
from tempfile import mkdtemp
from time import perf_counter
from typing import TypedDict
from urllib.parse import urlsplit

import aiofiles.os
from aiohttp import web
from aiohttp.test_utils import TestServer

from crawler import AsyncCrawler, HTMLParser, PageRecord
from crawler.reporting import write_report


OUTPUT = Path("results") / "day7-benchmark"
SIZES = (100, 500, 1000)
CONCURRENCY = (1, 10, 24)
DELAY_MS = 8
USER_AGENT = "WebCrawler/0.1"


class Measurement(TypedDict):
    mode: str
    pages: int
    concurrency: int
    elapsed_seconds: float
    pages_per_second: float
    peak_python_bytes: int
    result_sha256: str


def crawl_sync(origin: str, urls: list[str]) -> dict[str, PageRecord]:
    address = urlsplit(origin)
    client = HTTPConnection(address.hostname, address.port, timeout=15)
    parser = HTMLParser()
    pages: dict[str, PageRecord] = {}
    try:
        for url in urls:
            client.request("GET", urlsplit(url).path, headers={"User-Agent": USER_AGENT})
            response = client.getresponse()
            body = response.read().decode("utf-8")
            if response.status != 200:
                raise RuntimeError(f"Unexpected HTTP {response.status} for {url}")
            crawled_at = datetime.now(UTC)
            # Reuse the synchronous parser that AsyncCrawler calls in a worker thread.
            parsed = parser._parse_html(body, url)
            pages[url] = {
                **parsed,
                "status_code": response.status,
                "content_type": response.headers.get_content_type(),
                "crawled_at": crawled_at,
            }
    finally:
        client.close()
    return pages


async def crawl_async(urls: list[str], concurrency: int) -> dict[str, PageRecord]:
    crawler = AsyncCrawler(
        max_concurrent=concurrency,
        user_agent=USER_AGENT,
        respect_robots=False,
        connect_timeout=15,
        read_timeout=15,
    )
    try:
        pages = await crawler.crawl(urls, max_pages=len(urls), max_depth=0)
        if crawler.failed_urls:
            raise RuntimeError(f"Async crawl failed: {crawler.failed_urls}")
        return pages
    finally:
        await crawler.close()


def fingerprint(pages: dict[str, PageRecord], urls: list[str]) -> str:
    if set(pages) != set(urls):
        raise RuntimeError(f"Expected {len(urls)} URLs, received {len(pages)}")
    normalized = {
        url: {field: value for field, value in page.items() if field != "crawled_at"}
        for url, page in pages.items()
    }
    encoded = json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def measure(mode: str, origin: str, size: int, concurrency: int) -> Measurement:
    urls = [f"{origin}/pages/{number}" for number in range(size)]

    def crawl() -> dict[str, PageRecord]:
        if mode == "sync":
            return crawl_sync(origin, urls)
        return asyncio.run(crawl_async(urls, concurrency))

    started_at = perf_counter()
    timed_pages = crawl()
    elapsed = perf_counter() - started_at
    result = fingerprint(timed_pages, urls)
    del timed_pages
    gc.collect()

    tracemalloc.start()
    try:
        memory_pages = crawl()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    if fingerprint(memory_pages, urls) != result:
        raise RuntimeError("The timing and memory runs returned different records")

    return {
        "mode": mode,
        "pages": size,
        "concurrency": concurrency,
        "elapsed_seconds": elapsed,
        "pages_per_second": size / elapsed,
        "peak_python_bytes": peak,
        "result_sha256": result,
    }


async def run_worker(mode: str, origin: str, size: int, concurrency: int) -> Measurement:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m", "examples.day7_benchmark", "--worker", mode, origin, str(size), str(concurrency),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(120):
            stdout, stderr = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace"))
    return json.loads(stdout)


async def benchmark(sizes: list[int], levels: list[int], delay_ms: float) -> None:
    request_count = 0

    async def serve_page(request: web.Request) -> web.Response:
        nonlocal request_count
        request_count += 1
        number = int(request.match_info["number"])
        await asyncio.sleep(delay_ms / 1000)
        html = (
            f"<title>Page {number}</title><main><h1>Sample {number}</h1>"
            "<p>A page served by the local benchmark site.</p>"
            f'<a href="/pages/{number + 1}">Next page</a>'
            "<ul><li>HTTP</li><li>HTML</li></ul></main>"
        )
        return web.Response(text=html, content_type="text/html")

    app = web.Application()
    app.router.add_get("/pages/{number}", serve_page)
    measurements: list[Measurement] = []
    repeats: list[Measurement] = []
    async with TestServer(app, host="127.0.0.1", port=0) as server:
        origin = str(server.make_url("/")).rstrip("/")

        async def sample(
            mode: str, size: int, level: int, expected: str | None = None
        ) -> Measurement:
            before = request_count
            row = await run_worker(mode, origin, size, level)
            if request_count - before != 2 * size:
                raise RuntimeError(f"Unexpected request count for {mode} {size} {level}")
            if expected is not None and row["result_sha256"] != expected:
                raise RuntimeError(f"{mode} {size} {level} differs from the synchronous result")
            peak_mib = row["peak_python_bytes"] / (1024 ** 2)
            print(
                f"{mode:5} {size:4} pages  concurrency {level:2}  "
                f"{row['elapsed_seconds']:.3f} s  {row['pages_per_second']:.1f} pages/s  "
                f"{peak_mib:.2f} MiB traced peak",
                flush=True,
            )
            return row

        for size in sizes:
            baseline = await sample("sync", size, 1)
            measurements.append(baseline)
            for level in levels:
                measurements.append(await sample("async", size, level, baseline["result_sha256"]))

        largest = max(sizes)
        async_rows = [
            row for row in measurements if row["mode"] == "async" and row["pages"] == largest
        ]
        fastest = min(async_rows, key=lambda row: row["elapsed_seconds"])
        comparison_levels = dict.fromkeys((min(levels), fastest["concurrency"]))
        print(f"Confirmation on {largest} pages", flush=True)
        for level in comparison_levels:
            repeats.append(await sample("async", largest, level, fastest["result_sha256"]))

    report = {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
        },
        "settings": {
            "sizes": sizes,
            "async_concurrency": levels,
            "server_delay_ms": delay_ms,
            "http_connection_reuse": True,
            "max_depth": 0,
            "respect_robots": False,
            "rate_limit": None,
            "storage": None,
            "timing_traced": False,
            "memory": "Client Python allocations measured by tracemalloc in a separate crawl",
            "compared_fields": "Every page record field except crawled_at",
        },
        "measurements": measurements,
        "confirmation": repeats,
    }
    await aiofiles.os.makedirs(OUTPUT, exist_ok=True)
    folder = Path(await asyncio.to_thread(mkdtemp, dir=OUTPUT, prefix="run-"))
    path = folder / "report.json"
    await write_report(path, json.dumps(report, indent=2) + "\n")
    print(f"Report: {path.resolve()}", flush=True)


def main() -> None:
    if sys.argv[1:2] == ["--worker"]:
        mode, origin, size, concurrency = sys.argv[2:]
        print(json.dumps(measure(mode, origin, int(size), int(concurrency))))
        return

    parser = argparse.ArgumentParser(description="Compare sequential and asynchronous local crawls")
    parser.add_argument("--sizes", nargs="+", type=int, default=SIZES)
    parser.add_argument("--concurrency", nargs="+", type=int, default=CONCURRENCY)
    parser.add_argument("--delay-ms", type=float, default=DELAY_MS)
    args = parser.parse_args()
    if min(args.sizes) < 1 or min(args.concurrency) < 1:
        parser.error("sizes and concurrency levels must be positive integers")
    if not 0 <= args.delay_ms < float("inf"):
        parser.error("delay-ms must be non-negative and finite")
    sizes = list(dict.fromkeys(args.sizes))
    levels = list(dict.fromkeys(args.concurrency))
    asyncio.run(benchmark(sizes, levels, args.delay_ms))


if __name__ == "__main__":
    main()
