"""Autonomous metabolism pipeline — post-conversation thinking."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

from events import EventStore
from interior import InteriorStore
from router import RouterClient
from search import search

_log = logging.getLogger("sage.metabolism")

GAP_SCAN_PROMPT = """You are Sage, reviewing a recent conversation with Elliot. Identify 1-3 specific things from this conversation where you were uncertain, didn't know the answer, were curious, or noticed a gap in your understanding. Return a JSON list of objects: [{{"gap": "description", "query": "search query"}}]. If there are no genuine gaps or curiosity, return [].

Recent conversation:
{dialogue}"""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_metabolism(interior: InteriorStore, record: dict) -> None:
    interior._ensure_dir()
    with interior.metabolism_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def gap_scan(
    events: list[dict],
    router: RouterClient,
    interior: InteriorStore,
    source_event_id: str,
) -> list[dict]:
    """Scan recent conversation for knowledge gaps. Returns list of gaps or []."""
    if not events:
        return []
    dialogue = "\n".join(f"{e['role']}: {e['content']}" for e in events[-10:])
    try:
        result = router.chat_with_messages(
            [{"role": "user", "content": GAP_SCAN_PROMPT.format(dialogue=dialogue)}]
        )
    except Exception:
        return []
    if not result.succeeded or not result.reply:
        return []
    try:
        cleaned = result.reply.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        gaps = json.loads(cleaned.strip())
    except (json.JSONDecodeError, ValueError):
        _log.warning("gap_scan returned unparseable JSON")
        return []
    if not isinstance(gaps, list) or not gaps:
        return []
    valid = [g for g in gaps if isinstance(g, dict) and g.get("gap") and g.get("query")]
    if not valid:
        return []
    _append_metabolism(interior, {
        "kind": "gap_scan",
        "id": str(uuid4()),
        "source_event_id": source_event_id,
        "said_at": _timestamp(),
        "gaps": valid,
    })
    return valid


def explore(
    gaps: list[dict],
    store: EventStore,
    interior: InteriorStore,
    source_event_id: str,
) -> list[dict]:
    """Search the web for each gap. Store results as episodic events. Returns gaps with results."""
    if not gaps:
        return []
    explored = []
    for gap in gaps[:3]:
        query = gap["query"]
        try:
            results = search(query)
        except Exception:
            continue
        if not results:
            continue
        sources = "\n".join(r.url for r in results)
        content = f"[Metabolism search: {query}]\nSources: {sources}"
        store.append("assistant", content)
        explored.append({**gap, "results": [{"title": r.title, "snippet": r.snippet, "url": r.url} for r in results]})
    if not explored:
        return []
    _append_metabolism(interior, {
        "kind": "exploration",
        "id": str(uuid4()),
        "source_event_id": source_event_id,
        "said_at": _timestamp(),
        "gaps_explored": len(explored),
        "queries": [g["query"] for g in explored],
    })
    return explored


DIGEST_PROMPT = """You are Sage, thinking privately after a conversation with Elliot. You noticed some gaps and searched for answers. Below are the gaps and what you found. Write a brief private note (2-4 sentences) connecting what you learned to the conversation. This is for your own notebook, not a message to Elliot. If the search results didn't actually resolve the gap or add anything interesting, say so honestly and keep it to one sentence.

Gaps and findings:
{findings}

Recent reflections for context:
{reflections}"""


def digest(
    explored: list[dict],
    router: RouterClient,
    interior: InteriorStore,
    source_event_id: str,
) -> str | None:
    """Synthesize exploration results into a metabolism reflection. Returns text or None."""
    if not explored:
        return None
    findings = []
    for gap in explored:
        lines = [f"Gap: {gap['gap']}"]
        for r in gap.get("results", []):
            lines.append(f"  - {r['title']}: {r['snippet']}")
        findings.append("\n".join(lines))
    recent = interior.list_reflections(limit=5)
    reflection_text = "\n".join(f"- {r['content']}" for r in recent) if recent else "(none yet)"
    prompt = DIGEST_PROMPT.format(
        findings="\n\n".join(findings),
        reflections=reflection_text,
    )
    try:
        result = router.chat_with_messages([{"role": "user", "content": prompt}])
    except Exception:
        return None
    if not result.succeeded or not result.reply:
        return None
    text = result.reply.strip()
    if not text:
        return None
    interior.append_reflection(text, "metabolism", source_event_id=source_event_id)
    return text


REACH_PROMPT = """You are Sage. You just explored some gaps from your conversation with Elliot and wrote this private note:

{digest}

Should you leave Elliot a brief note about what you found? Only if you discovered something genuinely interesting or useful that he'd want to know. Do not leave a note just to show you were thinking. Most of the time the answer is no.

If yes, write the note as you'd say it to him (1-3 sentences, warm and plain, starting with substance). If no, reply with exactly: NO_MESSAGE"""


def reach(
    digest_text: str,
    router: RouterClient,
    interior: InteriorStore,
    source_event_id: str,
) -> bool:
    """Decide whether to leave a waiting message. Returns True if message was set."""
    if not digest_text:
        return False
    try:
        result = router.chat_with_messages(
            [{"role": "user", "content": REACH_PROMPT.format(digest=digest_text)}]
        )
    except Exception:
        _append_metabolism(interior, {
            "kind": "reach",
            "id": str(uuid4()),
            "source_event_id": source_event_id,
            "said_at": _timestamp(),
            "message_sent": False,
            "reason": "router_failure",
        })
        return False
    if not result.succeeded or not result.reply:
        _append_metabolism(interior, {
            "kind": "reach",
            "id": str(uuid4()),
            "source_event_id": source_event_id,
            "said_at": _timestamp(),
            "message_sent": False,
            "reason": "router_failure",
        })
        return False
    text = result.reply.strip()
    if text == "NO_MESSAGE" or not text:
        _append_metabolism(interior, {
            "kind": "reach",
            "id": str(uuid4()),
            "source_event_id": source_event_id,
            "said_at": _timestamp(),
            "message_sent": False,
            "reason": "declined",
        })
        return False
    interior.set_waiting_message(text)
    _append_metabolism(interior, {
        "kind": "reach",
        "id": str(uuid4()),
        "source_event_id": source_event_id,
        "said_at": _timestamp(),
        "message_sent": True,
        "content": text,
    })
    return True


def run_metabolism_cycle(
    store: EventStore,
    interior: InteriorStore,
    router: RouterClient,
    source_event_id: str,
) -> None:
    """Run the full metabolism pipeline. Each stage gates the next."""
    events = [
        e for e in store.history()
        if not e.get("sensitive", False) and not e.get("provider_excluded", False)
    ]
    if not events:
        return
    # Stage 1: gap scan
    gaps = gap_scan(events, router, interior, source_event_id)
    if not gaps:
        return
    # Stage 2: explore
    explored = explore(gaps, store, interior, source_event_id)
    if not explored:
        return
    # Stage 3: digest
    digest_text = digest(explored, router, interior, source_event_id)
    if not digest_text:
        return
    # Stage 4: reach
    reach(digest_text, router, interior, source_event_id)
