import httpx
from config.settings import (
    SEARXNG_URL,
    SEARCH_MAX_RESULTS,
    SEARCH_TIMEOUT_SECONDS,
)


def search(query: str) -> list[dict]:
    """Search via SearXNG. Returns list of {title, url, snippet} or [] on any failure."""
    if not query or not query.strip():
        return []

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

    results = data.get("results", [])
    if not isinstance(results, list):
        return []

    out = []
    for r in results[:SEARCH_MAX_RESULTS]:
        title = r.get("title", "").strip()
        url = r.get("url", "").strip()
        snippet = r.get("content", "").strip()
        if title and url and snippet:
            out.append({"title": title, "url": url, "snippet": snippet})

    return out