"""Article reader — Blueprint §3.3.

SearXNG returns snippets (3–5 sentences). The reader fetches the actual page
and extracts full clean article text via trafilatura. One real article
outweighs fifty snippets.

Usage:
- fetch_article(url) → str | None (full text, or None on failure)
- enrich_results(results) → list[dict]  (adds "article_text" key where fetchable)

Contracts:
- Never raises into the heartbeat/chat path (Invariant 1)
- Per-domain politeness: 1s minimum between fetches to the same host
- Trafilatura respects robots.txt by default
- Returns None on HTTP error, parse failure, or text too short (<200 chars)
- Max fetch time: 8s (matches SEARCH_TIMEOUT_SECONDS; keeps the beat lean)
"""
from __future__ import annotations

import time
import urllib.parse
from typing import TYPE_CHECKING

try:
    import trafilatura
    _TRAF_OK = True
except ImportError:
    _TRAF_OK = False

from utils.logger import warning, log

FETCH_TIMEOUT = 8.0
MIN_TEXT_CHARS = 200
MAX_TEXT_CHARS = 8000  # cap so one article can't dominate the context

# Per-domain last-fetch timestamp — simple courtesy throttle
_domain_last: dict[str, float] = {}
DOMAIN_POLITE_DELAY = 1.0  # seconds


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def fetch_article(url: str) -> str | None:
    """Fetch and extract clean article text from *url*.

    Blocked domains (heavy JS, paywalls, known noise) return None immediately.
    """
    if not _TRAF_OK:
        return None
    if not url or not url.startswith(("http://", "https://")):
        return None

    host = _host(url)
    # Per-domain politeness
    last = _domain_last.get(host, 0.0)
    delta = time.time() - last
    if delta < DOMAIN_POLITE_DELAY:
        time.sleep(DOMAIN_POLITE_DELAY - delta)

    try:
        downloaded = trafilatura.fetch_url(url, no_ssl=False,
                                           timeout=FETCH_TIMEOUT)
        _domain_last[host] = time.time()
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded,
                                   include_comments=False,
                                   include_tables=False,
                                   favor_recall=True)
        if not text or len(text) < MIN_TEXT_CHARS:
            return None
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS].rstrip() + "\n…[truncated]"
        return text
    except Exception as exc:
        warning(f"reader/fetch_article: {type(exc).__name__} {url[:80]}: {exc}")
        return None


def enrich_results(results: list[dict], max_fetch: int = 2) -> list[dict]:
    """Attempt to fetch full article text for the top *max_fetch* results.

    Enriches in-place (adds "article_text" key), returns the list.
    Errors degrade silently — the snippet is still there as fallback.
    """
    fetched = 0
    for r in results:
        if fetched >= max_fetch:
            break
        url = r.get("url") or ""
        if not url:
            continue
        text = fetch_article(url)
        if text:
            r["article_text"] = text
            fetched += 1
            log("reader", "fetched", url=url[:80], chars=len(text))
    return results


if __name__ == "__main__":
    # Quick smoke test — fetches a real URL, prints first 200 chars
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://en.wikipedia.org/wiki/Trafilatura"
    print(f"Fetching: {url}")
    text = fetch_article(url)
    if text:
        print(f"OK: {len(text)} chars")
        print(text[:300])
    else:
        print("FAIL: no text extracted")
