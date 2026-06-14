import re
import html
from urllib.parse import quote

import httpx
from config.settings import (
    SEARXNG_URL,
    SEARCH_MAX_RESULTS,
    SEARCH_TIMEOUT_SECONDS,
)

# Wikimedia and Semantic Scholar both *welcome* identified programmatic clients
# but block anonymous/default User-Agents. A descriptive UA with a contact link
# is what turns the "respect our robot policy" 403 into a normal 200.
USER_AGENT = "SageReflectionAgent/1.0 (https://github.com/91nedlawnavi-svg/sage)"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"


def _is_bare_qid(title: str) -> bool:
    # Wikidata entity label like "Q46600616": a 'Q' followed by all digits.
    return len(title) > 1 and title[0] == "Q" and title[1:].isdigit()


def _strip_html(text: str) -> str:
    """Strip HTML tags and unescape entities from a snippet."""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _searxng_search(query: str) -> list[dict]:
    """Broad web via local SearXNG. Best-effort: the upstream engines (Google,
    Bing, ...) frequently block SearXNG as a bot, so this can come back empty.
    Returns [] on any failure."""
    try:
        with httpx.Client(timeout=SEARCH_TIMEOUT_SECONDS) as client:
            resp = client.get(
                SEARXNG_URL,
                params={"q": query.strip(), "format": "json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    out = []
    # Primary: normal web-engine results.
    for r in data.get("results", [])[:SEARCH_MAX_RESULTS]:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("content") or "").strip()
        if title and url and snippet:
            out.append({"title": title, "url": url, "snippet": snippet})
    if out:
        return out

    # In-response fallback: the Wikidata/Wikipedia infobox (no extra request).
    candidates = []
    for ib in data.get("infoboxes", []):
        title = (ib.get("infobox") or "").strip()
        url = (ib.get("id") or "").strip()
        snippet = (ib.get("content") or "").strip()
        if not url and ib.get("urls"):
            url = (ib["urls"][0].get("url") or "").strip()
        if title and url:
            candidates.append({"title": title, "url": url, "snippet": snippet})
    candidates.sort(key=lambda c: (_is_bare_qid(c["title"]), not c["snippet"]))
    return candidates[:SEARCH_MAX_RESULTS]


def _wikipedia_search(query: str, limit: int) -> list[dict]:
    """Wikipedia full-text search. Free and reliable; Wikipedia allows us as long
    as we identify ourselves with a descriptive User-Agent. Returns [] on failure."""
    if not query or not query.strip():
        return []
    try:
        with httpx.Client(timeout=SEARCH_TIMEOUT_SECONDS) as client:
            resp = client.get(
                WIKIPEDIA_API,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query.strip(),
                    "srlimit": limit,
                    "format": "json",
                },
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    out = []
    for r in data.get("query", {}).get("search", []):
        title = (r.get("title") or "").strip()
        snippet = _strip_html(r.get("snippet") or "")
        if not title or not snippet:
            continue
        url = "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))
        out.append({"title": title, "url": url, "snippet": snippet})
    return out[:limit]


def _semantic_scholar_search(query: str, limit: int) -> list[dict]:
    """Semantic Scholar paper search. Free, no API key, ~200M papers across
    psychology / neuroscience / biology — a strong match for Sage's curiosity.
    Returns [] on failure (including rate-limit 429)."""
    if not query or not query.strip():
        return []
    try:
        with httpx.Client(timeout=SEARCH_TIMEOUT_SECONDS) as client:
            resp = client.get(
                SEMANTIC_SCHOLAR_API,
                params={
                    "query": query.strip(),
                    "limit": limit,
                    "fields": "title,abstract,url,year",
                },
                headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    out = []
    for p in data.get("data", []):
        title = (p.get("title") or "").strip()
        url = (p.get("url") or "").strip()
        if not title or not url:
            continue
        abstract = (p.get("abstract") or "").strip()
        year = p.get("year")
        snippet = abstract or (f"Academic paper ({year})." if year else "Academic paper.")
        if len(snippet) > 500:
            snippet = snippet[:500].rstrip() + "..."
        out.append({"title": title, "url": url, "snippet": snippet})
    return out[:limit]


def search(query: str) -> list[dict]:
    """Return up to SEARCH_MAX_RESULTS {title, url, snippet}, or [] only if every
    source fails.

    Strategy: try the broad web (SearXNG) first; if it's blocked/empty, fall back
    to the two free sources that welcome identified clients — Wikipedia (concepts)
    and Semantic Scholar (research) — blended so a finding carries both."""
    if not query or not query.strip():
        return []

    # 1) Broad web, best-effort.
    out = _searxng_search(query)
    if out:
        return out[:SEARCH_MAX_RESULTS]

    # 2) Reliable, bot-welcome free APIs. Blend concept + research.
    wiki = _wikipedia_search(query, SEARCH_MAX_RESULTS)
    papers = _semantic_scholar_search(query, SEARCH_MAX_RESULTS)
    blended = []
    for i in range(SEARCH_MAX_RESULTS):
        if i < len(wiki):
            blended.append(wiki[i])
        if i < len(papers):
            blended.append(papers[i])
    return blended[:SEARCH_MAX_RESULTS]
