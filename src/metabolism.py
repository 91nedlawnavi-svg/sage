"""Autonomous metabolism pipeline — post-conversation thinking."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

from interior import InteriorStore
from router import RouterClient

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
