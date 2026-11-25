import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from .advanced import AdvancedCrawler
from .config import CrawlerConfig, StorageConfig, load_config
from .logging_config import configure_logging


_OUTPUT_FORMATS = {
    ".csv": "csv",
    ".jsonl": "jsonl",
    ".db": "sqlite",
    ".sqlite": "sqlite",
    ".sqlite3": "sqlite",
}


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


async def _run(config: CrawlerConfig) -> int:
    crawler = AdvancedCrawler(config)
    try:
        await crawler.crawl()
    finally:
        await crawler.close()

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
    try:
        config = _resolve_config(args)
        with configure_logging(config):
            return asyncio.run(_run(config))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        return 130
