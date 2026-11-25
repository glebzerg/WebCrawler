from collections.abc import Iterable
from html import escape

from .stats import CrawlStats


_STYLES = """
    :root {
      color-scheme: light;
      color: #182534;
      background: #f2f5f9;
      font: 16px/1.5 "Segoe UI", system-ui, sans-serif;
    }
    body { margin: 0; }
    main { max-width: 68rem; margin: 0 auto; padding: clamp(1rem, 4vw, 3rem); }
    h1 { margin: 0 0 1.25rem; font-size: clamp(1.75rem, 4vw, 2.5rem); }
    h2 { margin: 0 0 .85rem; font-size: 1.15rem; }
    section { min-width: 0; padding: 1.25rem; background: white; border: 1px solid #dce4ee;
              border-radius: .75rem; margin-bottom: 1rem; }
    .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
               gap: .75rem; margin: 0; }
    .metrics div { padding: .75rem; background: #f2f5f9; border-radius: .5rem; }
    dt { color: #465971; font-size: .9rem; }
    dd { margin: 0; font-size: 1.5rem; font-weight: 650; }
    .tables { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 23rem), 1fr));
              gap: 1rem; }
    table { width: 100%; border-collapse: collapse; text-align: left; overflow-wrap: anywhere; }
    th, td { padding: .55rem .5rem; border-bottom: 1px solid #e3eaf2; vertical-align: top; }
    thead th { color: #465971; font-size: .85rem; }
    tbody th { font-weight: 550; }
    tbody tr:last-child > * { border-bottom: 0; }
    meter { min-width: 4rem; width: 100%; height: 1rem; }
    meter::-webkit-meter-bar { background: #e3eaf2; border: 0; border-radius: .25rem; }
    meter::-webkit-meter-optimum-value,
    meter::-webkit-meter-suboptimum-value,
    meter::-webkit-meter-even-less-good-value { background: #2563a0; border-radius: .25rem; }
    meter::-moz-meter-bar { background: #2563a0; }
    .empty { color: #465971; }
"""


def _text_rows(items: Iterable[tuple[str, int | str]]) -> str:
    return "".join(
        f'<tr><th scope="row">{escape(label)}</th><td>{escape(str(value))}</td></tr>'
        for label, value in items
    ) or '<tr><td colspan="2" class="empty">No data</td></tr>'


def _status_rows(counts: dict[str, int]) -> str:
    maximum = max(counts.values(), default=1)
    rows = []
    for status, count in sorted(counts.items()):
        label = escape(status)
        rows.append(
            f'<tr><th scope="row">{label}</th><td>{count}</td>'
            f'<td><meter min="0" max="{maximum}" value="{count}" '
            f'aria-label="Responses with HTTP status {label}">{count}</meter></td></tr>'
        )
    return "".join(rows) or '<tr><td colspan="3" class="empty">No HTTP responses</td></tr>'


def render_html_report(stats: CrawlStats) -> str:
    """Build a self-contained report from the crawler's statistics."""
    metrics = {
        "Total pages": stats["total_pages"],
        "Processed": stats["processed_pages"],
        "Failed": stats["failed_pages"],
        "Blocked": stats["blocked_pages"],
        "Saved": stats["saved_pages"],
        "Elapsed (s)": f"{stats['elapsed_seconds']:.3f}",
        "Pages per second": f"{stats['pages_per_second']:.2f}",
    }
    cards = "".join(f"<div><dt>{name}</dt><dd>{value}</dd></div>" for name, value in metrics.items())
    requests = {
        "Attempts": stats["http_attempts"],
        "Successful": stats["successful_requests"],
        "Failed": stats["failed_requests"],
        "Successful retries": stats["successful_retries"],
    }
    errors = list(stats["permanent_errors"].items()) + list(stats["storage_errors"].items())
    if stats["storage_close_error"] is not None:
        errors.append(("Storage close", stats["storage_close_error"]))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crawl report</title>
  <style>{_STYLES}</style>
</head>
<body>
<main>
  <h1>Crawl report</h1>
  <section aria-labelledby="overview">
    <h2 id="overview">Overview</h2>
    <dl class="metrics">{cards}</dl>
  </section>
  <div class="tables">
    <section aria-labelledby="statuses">
      <h2 id="statuses">HTTP status distribution</h2>
      <table id="status-counts">
        <thead><tr><th scope="col">Status</th><th scope="col">Responses</th>
          <th scope="col">Distribution</th></tr></thead>
        <tbody>{_status_rows(stats["status_counts"])}</tbody>
      </table>
    </section>
    <section aria-labelledby="requests">
      <h2 id="requests">HTTP requests</h2>
      <table>
        <thead><tr><th scope="col">Outcome</th><th scope="col">Count</th></tr></thead>
        <tbody>{_text_rows(requests.items())}</tbody>
      </table>
    </section>
    <section aria-labelledby="domains">
      <h2 id="domains">Domains</h2>
      <table id="top-domains">
        <thead><tr><th scope="col">Domain</th><th scope="col">Processed pages</th></tr></thead>
        <tbody>{_text_rows(stats["top_domains"].items())}</tbody>
      </table>
    </section>
    <section aria-labelledby="error-types">
      <h2 id="error-types">Request errors by type</h2>
      <table>
        <thead><tr><th scope="col">Type</th><th scope="col">Count</th></tr></thead>
        <tbody>{_text_rows(stats["errors_by_type"].items())}</tbody>
      </table>
    </section>
  </div>
  <section aria-labelledby="errors">
    <h2 id="errors">Permanent and storage errors</h2>
    <table id="reported-errors">
      <thead><tr><th scope="col">Source</th><th scope="col">Message</th></tr></thead>
      <tbody>{_text_rows(errors)}</tbody>
    </table>
  </section>
</main>
</body>
</html>
"""
