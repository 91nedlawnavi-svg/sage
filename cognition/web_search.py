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

    out = []

    # Primary: normal web-engine results.
    for r in data.get("results", [])[:SEARCH_MAX_RESULTS]:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("content") or "").strip()
        if title and url and snippet:
            out.append({"title": title, "url": url, "snippet": snippet})

    # Fallback: when the rate-limited engines return nothing, use the
    # Wikipedia / Wikidata infobox from the SAME response (no rate limits).
    if not out:
        for ib in data.get("infoboxes", []):
            title = (ib.get("infobox") or "").strip()
            url = (ib.get("id") or "").strip()
            snippet = (ib.get("content") or "").strip()
            if not url and ib.get("urls"):
                url = (ib["urls"][0].get("url") or "").strip()
            if title and url:
                out.append({"title": title, "url": url, "snippet": snippet})
                if len(out) >= SEARCH_MAX_RESULTS:
                    break

    return out