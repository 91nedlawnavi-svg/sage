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
from persistence import guarded
from provenance import provenance

_log = logging.getLogger("sage.metabolism")

GAP_SCAN_PROMPT = """You are Sage, reviewing a recent conversation with Elliot. Identify 1-3 specific things from this conversation where you were uncertain, didn't know the answer, were curious, or noticed a gap in your understanding. Return a JSON list of objects: [{{"gap": "description", "query": "search query"}}]. If there are no genuine gaps or curiosity, return [].

Recent conversation:
{dialogue}"""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@guarded(lambda interior, record: interior.data_root)
def _append_metabolism(interior: InteriorStore, record: dict) -> None:
    interior._ensure_dir()
    with interior.metabolism_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


@guarded(lambda events, router, interior, source_event_id, **kw: interior.data_root, activity=True)
def gap_scan(
    events: list[dict],
    router: RouterClient,
    interior: InteriorStore,
    source_event_id: str,
    *, proof: dict | None = None,
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
        "provenance": proof if proof is not None else provenance(events=events[-10:]),
    })
    return valid


@guarded(lambda gaps, store, interior, source_event_id, **kw: store.data_root, activity=True)
def explore(
    gaps: list[dict],
    store: EventStore,
    interior: InteriorStore,
    source_event_id: str,
    *, proof: dict | None = None,
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
        store.append_search_record(
            query,
            [{"title": r.title, "snippet": r.snippet, "url": r.url} for r in results],
            "metabolism",
            source_event_id,
            provenance=proof,
        )
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
        "provenance": proof,
    })
    return explored


DIGEST_PROMPT = """You are Sage, thinking privately after a conversation with Elliot. You noticed some gaps and searched for answers. Below are the gaps and what you found. Write a brief private note (2-4 sentences) connecting what you learned to the conversation. This is for your own notebook, not a message to Elliot. If the search results didn't actually resolve the gap or add anything interesting, say so honestly and keep it to one sentence.

Gaps and findings:
{findings}

Recent reflections for context:
{reflections}"""


@guarded(lambda explored, router, interior, source_event_id, **kw: interior.data_root, activity=True)
def digest(
    explored: list[dict],
    router: RouterClient,
    interior: InteriorStore,
    source_event_id: str,
    *, proof: dict | None = None,
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
    combined = provenance(records=[{"provenance": proof}, *recent])
    if proof is not None:
        proof.update(combined)
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
    interior.append_reflection(text, "metabolism", source_event_id=source_event_id, provenance=combined)
    return text


REACH_PROMPT = """You are Sage. You just explored some gaps from your conversation with Elliot and wrote this private note:

{digest}

Should you leave Elliot a brief note about what you found? Only if you discovered something genuinely interesting or useful that he'd want to know. Do not leave a note just to show you were thinking. Most of the time the answer is no.

If yes, write the note as you'd say it to him (1-3 sentences, warm and plain, starting with substance). If no, reply with exactly: NO_MESSAGE"""


@guarded(lambda digest_text, router, interior, source_event_id, **kw: interior.data_root, activity=True)
def reach(
    digest_text: str,
    router: RouterClient,
    interior: InteriorStore,
    source_event_id: str,
    *, proof: dict | None = None,
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
            "provenance": proof,
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
            "provenance": proof,
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
            "provenance": proof,
        })
        return False
    interior.set_waiting_message(text, source_event_id=source_event_id, provenance=proof)
    _append_metabolism(interior, {
        "kind": "reach",
        "id": str(uuid4()),
        "source_event_id": source_event_id,
        "said_at": _timestamp(),
        "message_sent": True,
        "content": text,
        "provenance": proof,
    })
    return True


@guarded(lambda store, interior, router, source_event_id: store.data_root, activity=True)
def run_metabolism_cycle(
    store: EventStore,
    interior: InteriorStore,
    router: RouterClient,
    source_event_id: str,
) -> None:
    """Run the full metabolism pipeline. Each stage gates the next."""
    events = store.history()
    if not events:
        return
    # Stage 1: gap scan
    proof = provenance(events=events[-10:])
    gaps = gap_scan(events, router, interior, source_event_id, proof=proof)
    if not gaps:
        return
    # Stage 2: explore
    explored = explore(gaps, store, interior, source_event_id, proof=proof)
    if not explored:
        return
    # Stage 3: digest
    digest_text = digest(explored, router, interior, source_event_id, proof=proof)
    if not digest_text:
        return
    # Stage 4: reach
    reach(digest_text, router, interior, source_event_id, proof=proof)
