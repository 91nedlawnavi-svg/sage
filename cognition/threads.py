"""Thread cognition — Blueprint §3.1 / §3.2.

Thread = open question with heat. Threads unify beliefs (stance-threads),
Elliot's world (fact-threads), gaps, findings, and the anti-basin portfolio.

This module handles:
- Opening threads from gaps (gap-spawned threads, §2.4)
- Feeding threads when a finding is topically related
- Decay + portfolio floor check (§3.5)
- Hot-thread query for the heartbeat burst decision (§3.2)

All writes go through relational_api so the writer queue serializes them.
Degrades gracefully — every function catches exceptions and returns empty/None.
"""
from __future__ import annotations

from utils.logger import log, warning


async def spawn_from_gap(gap: dict) -> str | None:
    """Open a thread for a gap, if one doesn't already exist for this gap_id.

    'gaps about Elliot's world auto-spawn threads' (§2.4)
    Returns thread id or None.
    """
    try:
        from memory.relational_api import open_thread, query
        from memory.sqlite_core import query as db_query
        gap_id = gap.get("id")
        desc = (gap.get("description") or "").strip()
        if not gap_id or not desc:
            return None
        # Idempotent: don't re-spawn if one already exists for this gap
        existing = db_query("relational",
            "SELECT id FROM threads WHERE spawned_from=? AND spawn_kind='gap' "
            "AND status='open'", (gap_id,))
        if existing:
            return existing[0]["id"]
        question = f"What do I know about: {desc}?"
        tid = await open_thread(question, spawned_from=gap_id, spawn_kind="gap")
        log("threads", "opened-from-gap", gap=gap_id, question=question[:80])
        return tid
    except Exception as exc:
        warning(f"threads/spawn_from_gap: {exc}")
        return None


async def feed_from_finding(query_text: str, results: list[dict]) -> int:
    """Feed open threads whose question overlaps the search query.

    Simple lexical overlap for now (shared significant words). Returns count fed.
    The e5 embedding would be more precise but this runs on the heartbeat
    hot path and we already paid for one embed per search.
    """
    try:
        from memory.relational_api import hot_threads, feed_thread
        if not query_text or not results:
            return 0
        threads = hot_threads(limit=10)
        if not threads:
            return 0

        # Tokenize query to significant words (>3 chars, skip stop words)
        _STOP = frozenset({"what", "when", "where", "which", "about", "does",
                           "have", "from", "that", "this", "with", "into",
                           "some", "know", "how", "why", "who", "the", "and",
                           "for", "are", "was", "been", "will", "can"})
        q_words = {w.lower() for w in query_text.split() if len(w) > 3} - _STOP

        fed = 0
        for t in threads:
            t_words = {w.lower() for w in (t.get("question") or "").split()
                       if len(w) > 3} - _STOP
            if q_words & t_words:  # any overlap
                await feed_thread(t["id"], heat_delta=0.25)
                fed += 1
        if fed:
            log("threads", "fed-from-finding", n=fed, query=query_text[:60])
        return fed
    except Exception as exc:
        warning(f"threads/feed_from_finding: {exc}")
        return 0


async def decay_and_check_portfolio() -> dict:
    """Heartbeat quiet-slot job: decay, stale dead threads, check portfolio.

    Portfolio floor (§3.5): no thread should hold > THREAD_PORTFOLIO_FLOOR of
    total heat. If one does, log a warning (Phase E's rhythm will act on it).

    Returns summary dict for logging.
    """
    try:
        from memory.relational_api import (
            decay_threads, all_open_threads, THREAD_PORTFOLIO_FLOOR,
        )
        staled = await decay_threads()
        threads = all_open_threads()
        total_heat = sum(t["heat"] for t in threads) or 1.0

        monopoly = [t for t in threads
                    if t["heat"] / total_heat > THREAD_PORTFOLIO_FLOOR]
        if monopoly:
            log("threads", "portfolio-monopoly",
                thread=monopoly[0]["question"][:60],
                share=round(monopoly[0]["heat"] / total_heat, 2))

        return {
            "open": len(threads),
            "staled": staled,
            "monopoly": len(monopoly),
            "total_heat": round(total_heat, 2),
        }
    except Exception as exc:
        warning(f"threads/decay_and_check_portfolio: {exc}")
        return {}


def hottest_thread() -> dict | None:
    """Return the hottest open thread, or None. Used by heartbeat to decide burst."""
    try:
        from memory.relational_api import hot_threads
        threads = hot_threads(limit=1)
        return threads[0] if threads else None
    except Exception:
        return None
