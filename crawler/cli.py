import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from .advanced import AdvancedCrawler
from .config import CrawlerConfig, StorageConfig, load_config
from .core import CrawlProgress
from .logging_config import configure_logging
from .reporting import same_destination


_OUTPUT_FORMATS = {
    ".csv": "csv",
    ".jsonl": "jsonl",
    ".db": "sqlite",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
}
_PROGRESS_INTERVAL = 1.0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crawler",
        description="Crawl web pages using command-line options or a JSON configuration.",
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument("--config", type=Path, help="path to a JSON configuration file")
    parser.add_argument("--urls", dest="start_urls", nargs="+", metavar="URL", help="start URLs")
    parser.add_argument("--max-pages", type=int, help="maximum number of pages to process")
    parser.add_argument("--max-depth", type=int, help="maximum depth from the start URLs")
    parser.add_argument("--output", metavar="FILE", help="file for the crawled page records")
    parser.add_argument("--stats-json", type=Path, metavar="FILE", help="write crawl statistics as JSON")
    parser.add_argument("--report-html", type=Path, metavar="FILE", help="write a standalone HTML report")
    parser.add_argument(
        "--respect-robots",
        action=argparse.BooleanOptionalAction,
        help="follow robots.txt rules",
    )
    parser.add_argument("--rate-limit", type=float, metavar="RATE", help="requests per second")
    return parser


def _resolve_config(args: argparse.Namespace) -> CrawlerConfig:
    options = vars(args).copy()
    path = options.pop("config", None)
    output = options.pop("output", None)
    config = load_config(path) if path is not None else CrawlerConfig()
    if output is not None:
        if config.storage is not None:
            options["storage"] = replace(config.storage, path=output)
        else:
            format_name = _OUTPUT_FORMATS.get(Path(output).suffix.lower(), "json")
            options["storage"] = StorageConfig(path=output, format=format_name)
    return replace(config, **options)


def _check_report_paths(
    config: CrawlerConfig, config_path: Path | None, reports: dict[str, Path]
) -> None:
    protected = [
        Path(path)
        for path in (config_path, config.log_file, config.storage.path if config.storage else None)
        if path is not None
    ]
    for path in reports.values():
        if any(same_destination(path, other) for other in protected):
            raise ValueError("Reports, configuration, page storage, and log must use different files")
        protected.append(path)


async def _run(
    config: CrawlerConfig, *, stats_json: Path | None = None, report_html: Path | None = None
) -> int:
    crawler = AdvancedCrawler(config)
    last_reported = float("-inf")

    def show_progress(progress: CrawlProgress) -> None:
        nonlocal last_reported
        now = perf_counter()
        if not progress["finished"] and now - last_reported < _PROGRESS_INTERVAL:
            return
        last_reported = now

        completed = progress["processed"] + progress["failed"]
        phase = "Finished" if progress["finished"] else "Crawling"
        eta = progress["eta_seconds"]
        estimate = f"{eta:.1f} s" if eta is not None else "n/a"
        print(
            f"{phase} {completed}/{progress['max_pages']} pages "
            f"({progress['budget_percent']:.1f}% of budget) | "
            f"Active pages {progress['in_progress']} | "
            f"active requests {progress['active_requests']} | "
            f"{progress['pages_per_second']:.2f} pages/s | ETA {estimate}",
            flush=True,
        )

    try:
        await crawler.crawl(on_progress=show_progress, progress_interval=_PROGRESS_INTERVAL)
    finally:
        await crawler.close()

    if stats_json is not None:
        await crawler.export_to_json(stats_json)
    if report_html is not None:
        await crawler.export_to_html_report(report_html)

    stats = crawler.get_stats()
    print(
        f"Processed {stats['processed_pages']} pages | "
        f"{stats['failed_pages']} failed | {stats['blocked_pages']} blocked"
    )
    if config.storage is not None:
        print(f"Saved {stats['saved_pages']} pages to {config.storage.path}")
    return 1 if stats["storage_errors"] or stats["storage_close_error"] else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    options = vars(args)
    reports = {name: options.pop(name) for name in ("stats_json", "report_html") if name in options}
    try:
        config = _resolve_config(args)
        _check_report_paths(config, options.get("config"), reports)
        with configure_logging(config):
            return asyncio.run(_run(config, **reports))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        return 130
