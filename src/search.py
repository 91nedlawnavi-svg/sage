"""Lightweight web search for Sage via DuckDuckGo HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SEARCH_URL: Final = "https://html.duckduckgo.com/html/"
USER_AGENT: Final = "Mozilla/5.0 (compatible; Sage/1.0; +local)"
MAX_RESULTS: Final = 5
TIMEOUT: Final = 15


@dataclass(frozen=True)
class SearchResult:
    title: str
    snippet: str
    url: str


class _ResultParser(HTMLParser):
    """Extract result links and snippets from DuckDuckGo HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._in_result_link = False
        self._in_snippet = False
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: v for k, v in attrs}
        cls = attr_dict.get("class", "") or ""

        if tag == "a" and "result__a" in cls:
            href = attr_dict.get("href", "")
            if href:
                self._current = {"url": href, "title": "", "snippet": ""}
                self._in_result_link = True

        if tag == "a" and "result__snippet" in cls and self._current is not None:
            self._in_snippet = True
            self._snippet_parts = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._in_result_link and self._current is not None:
            self._current["title"] += text
        if self._in_snippet:
            self._snippet_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_result_link:
            self._in_result_link = False
        if tag == "a" and self._in_snippet:
            self._in_snippet = False
            if self._current is not None:
                self._current["snippet"] = " ".join(self._snippet_parts)
                self.results.append(self._current)
                self._current = None
                self._snippet_parts = []


def search(query: str, max_results: int = MAX_RESULTS) -> list[SearchResult]:
    """Search DuckDuckGo and return structured results with provenance."""
    cleaned = query.strip()
    if not cleaned:
        return []

    payload = f"q={quote_plus(cleaned)}".encode()
    request = Request(
        SEARCH_URL,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            html = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, OSError):
        return []

    parser = _ResultParser()
    try:
        parser.feed(html)
    except Exception:
        return []

    seen_urls: set[str] = set()
    results: list[SearchResult] = []
    for item in parser.results:
        url = item.get("url", "").strip()
        title = item.get("title", "").strip()
        snippet = item.get("snippet", "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append(SearchResult(title=title, snippet=snippet, url=url))
        if len(results) >= max_results:
            break

    return results


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
