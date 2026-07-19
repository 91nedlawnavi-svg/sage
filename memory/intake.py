"""SQLite intake mirror — Wave 2 wiring, Blueprint §2.1.

"Everything Elliot says lands as an episode. Zero intelligence at intake."

Each hook mirrors one JSONL append into the matching SQLite store. JSONL
stays the export format (blueprint §2) and keeps being written by the
legacy log modules; this module only adds the episode row.

Contracts:
- No-op unless MEMORY_CORE_SQLITE (safe to call unconditionally).
- source_key matches what migrate_jsonl_to_sqlite derives from the JSONL
  entry, so migration + live intake never duplicate (INSERT OR IGNORE
  semantics via add_episode's dedup).
- Never raises into chat/heartbeat (Invariant 1).
"""
from __future__ import annotations

from config.settings import MEMORY_CORE_SQLITE
from utils.logger import warning


async def record_chat_turn(entry: dict | None) -> None:
    """Mirror one conversation-log entry (as returned by append_message)."""
    if not MEMORY_CORE_SQLITE or not entry:
        return
    try:
        from memory import relational_api as rel
        from cognition.held_close_sense import sense as _hc_sense
        speaker = "elliot" if entry["role"] == "user" else "sage"
        episode_id = await rel.add_episode(
            source="conversation",
            speaker=speaker,
            content=entry["content"],
            ts=entry["ts"],
            source_key=entry["id"],
        )
        # Span sensing: only Elliot turns trigger held-close logic (§2.8)
        if episode_id and speaker == "elliot":
            held = _hc_sense(entry["content"])
            if held:
                await rel.set_held_close(episode_id, True, "she-sensed", actor="she")
    except Exception as exc:
        warning(f"intake/chat_turn: {type(exc).__name__}: {exc}")


async def record_reflection(text: str, ts: str) -> None:
    if not MEMORY_CORE_SQLITE or not text:
        return
    try:
        from memory import interior_api
        await interior_api.add_episode(
            source="reflection", content=text, ts=ts, source_key=f"refl:{ts}")
    except Exception as exc:
        warning(f"intake/reflection: {type(exc).__name__}: {exc}")


async def record_finding(query: str, results: list[dict], ts: str) -> None:
    if not MEMORY_CORE_SQLITE or not query:
        return
    try:
        from memory import interior_api
        titles = [(r.get("title") or "").strip() for r in (results or [])][:3]
        content = f"Curiosity: {query}"
        if any(titles):
            content += ". Found: " + "; ".join(t for t in titles if t)
        await interior_api.add_episode(
            source="finding", content=content, ts=ts, source_key=f"find:{ts}")
    except Exception as exc:
        warning(f"intake/finding: {type(exc).__name__}: {exc}")
