import argparse
import asyncio
from pathlib import Path

from crawler import AdvancedCrawler


async def run(config: Path, report_dir: Path) -> int:
    crawler = AdvancedCrawler.from_config(config)
    try:
        await crawler.crawl()
    finally:
        await crawler.close()

    await crawler.export_to_json(report_dir / "python-stats.json")
    await crawler.export_to_html_report(report_dir / "python-report.html")
    stats = crawler.get_stats()
    print(f"Saved {stats['saved_pages']} pages | {stats['blocked_pages']} blocked")
    return 1 if stats["storage_errors"] or stats["storage_close_error"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a configured crawl through the Python API")
    parser.add_argument("config", type=Path, nargs="?", default=Path("examples/crawler.example.json"))
    parser.add_argument("--report-dir", type=Path, default=Path("results/quickstart"))
    args = parser.parse_args()
    return asyncio.run(run(args.config, args.report_dir))


if __name__ == "__main__":
    raise SystemExit(main())
