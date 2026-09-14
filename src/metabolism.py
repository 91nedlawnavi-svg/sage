"""Autonomous metabolism pipeline — post-conversation thinking."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from events import EventStore
from interior import InteriorStore
from router import RouterClient
from search import search
from persistence import append_jsonl, guarded, read_jsonl
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
    append_jsonl(interior.metabolism_path, [record])


def _metabolism_record(interior: InteriorStore, kind: str, source_event_id: str) -> dict | None:
    return next((
        record for record in reversed(read_jsonl(interior.metabolism_path))
        if isinstance(record, dict)
        and record.get("kind") == kind
        and record.get("source_event_id") == source_event_id
    ), None)


@guarded(lambda events, router, interior, source_event_id, **kw: interior.data_root, activity=True)
def gap_scan(
    events: list[dict],
    router: RouterClient,
    interior: InteriorStore,
    source_event_id: str,
    *, proof: dict | None = None,
) -> list[dict] | None:
    """Scan recent conversation for gaps. None means failure; [] means no gaps."""
    if not events:
        return []
    existing = _metabolism_record(interior, "gap_scan", source_event_id)
    if existing is not None and isinstance(existing.get("gaps"), list):
        return existing["gaps"]
    dialogue = "\n".join(f"{e['role']}: {e['content']}" for e in events[-10:])
    try:
        result = router.chat_with_messages(
            [{"role": "user", "content": GAP_SCAN_PROMPT.format(dialogue=dialogue)}]
        )
    except Exception:
        return None
    if not result.succeeded or not result.reply:
        return None
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
        return None
    if not isinstance(gaps, list):
        return None
    if not gaps:
        return []
    if any(
        not isinstance(gap, dict)
        or any(not isinstance(gap.get(field), str) or not gap[field].strip()
               for field in ("gap", "query"))
        for gap in gaps
    ):
        return None
    valid = [{**gap, "gap": gap["gap"].strip(), "query": gap["query"].strip()} for gap in gaps]
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
) -> list[dict] | None:
    """Search each gap. None means failure; [] means successful empty results."""
    if not gaps:
        return []
    explored = []
    existing_searches = {
        record["query"]: record["sources"]
        for record in store.search_records()
        if record.get("origin") == "metabolism"
        and record.get("source_event_id") == source_event_id
    }
    for gap in gaps[:3]:
        query = gap["query"]
        if query in existing_searches:
            explored.append({**gap, "results": existing_searches[query]})
            continue
        try:
            results = search(query)
        except Exception:
            return None
        # Cross-batch contract: SAGE-026 owns response parsing. A failed result
        # stays equal to [] for fail-soft callers, but carries failed=True;
        # a plain empty list is a successful search with no usable results.
        if results is None or getattr(results, "failed", False):
            return None
        if not results:
            continue
        try:
            sources = [{"title": r.title, "snippet": r.snippet, "url": r.url} for r in results]
        except (AttributeError, TypeError):
            return None
        store.append_search_record(
            query, sources, "metabolism", source_event_id, provenance=proof,
        )
        explored.append({**gap, "results": sources})
    if not explored:
        return []
    if _metabolism_record(interior, "exploration", source_event_id) is None:
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
    existing = next((
        reflection for reflection in reversed(interior.list_reflections(limit=10_000))
        if reflection.get("source_event_id") == source_event_id
        and reflection.get("category") == "metabolism"
    ), None)
    if existing is not None:
        if proof is not None:
            proof.update(provenance(records=[{"provenance": proof}, existing]))
        return existing["content"]
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
) -> bool | None:
    """Decide whether to leave a message. None means failure; False means decline."""
    if not digest_text:
        return False
    existing = _metabolism_record(interior, "reach", source_event_id)
    if existing is not None and existing.get("reason") != "router_failure":
        return bool(existing.get("message_sent"))
    waiting = interior.get_waiting_message()
    if waiting is not None and waiting.get("source_event_id") == source_event_id:
        _append_metabolism(interior, {
            "kind": "reach",
            "id": str(uuid4()),
            "source_event_id": source_event_id,
            "said_at": _timestamp(),
            "message_sent": True,
            "content": waiting["content"],
            "provenance": proof,
        })
        return True
    try:
        result = router.chat_with_messages(
            [{"role": "user", "content": REACH_PROMPT.format(digest=digest_text)}]
        )
    except Exception:
        return None
    if not result.succeeded or not result.reply:
        return None
    text = result.reply.strip()
    if not text:
        return None
    if text == "NO_MESSAGE":
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
) -> bool:
    """Run the full metabolism pipeline. Each stage gates the next."""
    events = store.history()
    if not events:
        return True
    # Stage 1: gap scan
    proof = provenance(events=events[-10:])
    gaps = gap_scan(events, router, interior, source_event_id, proof=proof)
    if gaps is None:
        return False
    if not gaps:
        return True
    # Stage 2: explore
    explored = explore(gaps, store, interior, source_event_id, proof=proof)
    if explored is None:
        return False
    if not explored:
        return True
    # Stage 3: digest
    digest_text = digest(explored, router, interior, source_event_id, proof=proof)
    if not digest_text:
        return False
    # Stage 4: reach
    return reach(digest_text, router, interior, source_event_id, proof=proof) is not None
