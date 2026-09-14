"""Lightweight web search for Sage via SearXNG JSON API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.client import IncompleteRead
from typing import Final
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SEARCH_URL: Final = "http://localhost:8080/search"
USER_AGENT: Final = "Mozilla/5.0 (compatible; Sage/1.0; +local)"
MAX_RESULTS: Final = 5
TIMEOUT: Final = 15


@dataclass(frozen=True)
class SearchResult:
    title: str
    snippet: str
    url: str


class SearchResults(list[SearchResult]):
    """Search results carrying whether an empty result means provider failure."""

    def __init__(self, values: list[SearchResult] | None = None, *, failed: bool = False) -> None:
        super().__init__(values or [])
        self.failed = failed


def search(query: str, max_results: int = MAX_RESULTS) -> SearchResults:
    """Search SearXNG and return structured results with provenance."""
    cleaned = query.strip()
    if not cleaned:
        return SearchResults()

    url = f"{SEARCH_URL}?q={quote_plus(cleaned)}&format=json"
    request = Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, OSError, IncompleteRead, json.JSONDecodeError):
        return SearchResults(failed=True)

    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return SearchResults(failed=True)

    results: list[SearchResult] = []
    for item in data.get("results", [])[:max_results]:
        if not isinstance(item, dict):
            return SearchResults(failed=True)
        if any(value is not None and not isinstance(value, str) for value in (
            item.get("url"), item.get("title"), item.get("content"),
        )):
            return SearchResults(failed=True)
        u = (item.get("url") or "").strip()
        t = (item.get("title") or "").strip()
        s = (item.get("content") or "").strip()
        if not u:
            continue
        results.append(SearchResult(title=t, snippet=s, url=u))

    return SearchResults(results)


def format_search_context(results: list[SearchResult]) -> str:
    """Format search results as context for the router prompt."""
    if not results:
        return ""
    lines = ["[Web search results]"]
    for i, result in enumerate(results, 1):
        lines.append(f"{i}. {result.title}")
        if result.snippet:
            lines.append(f"   {result.snippet}")
        lines.append(f"   Source: {result.url}")
    lines.append("[End search results]")
    return "\n".join(lines)
