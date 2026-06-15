"""Semantic recall — Phase 4 Layer 1.

Relevance-based long-term memory: embeds past conversation turns and Sage's own
reflections, then at chat time retrieves the ones most semantically relevant to
the current message — including those that have scrolled out of the live context
window. Complements the Membrane (which surfaces *recent* reflections by
recency) and the in-RAM history window (Layer 0).

Design:
  - A persisted, append-only index at ~/sage_data/recall_index.jsonl
    ({key, kind, ts, text, embedding, role?}). Each turn/reflection is embedded
    exactly once.
  - Indexing runs in the background off the heartbeat in throttled batches, so
    the e5 sequential-hang quirk never bites and the chat path never pays for
    bulk embedding.
  - Retrieval embeds only the incoming message (one call), cosine-ranks the
    index, drops anything already visible (recent turns / Membrane-recent
    reflections), and returns a bounded, clearly-delimited block.
  - Degrades silently to None on any failure (e5 down, empty index, etc.) —
    never raises into the chat path, same contract as the Membrane.
"""
import asyncio
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from config.settings import (
    RECALL_ENABLED,
    RECALL_INDEX_PATH,
    RECALL_TOP_K,
    RECALL_MIN_SIM,
    RECALL_MAX_CHARS,
    RECALL_MIN_CHARS,
    RECALL_RECENT_EXCLUDE_TURNS,
    RECALL_RECENT_HOURS,
    RECALL_INDEX_BATCH,
    RECALL_EMBED_SLEEP,
    RECALL_REFLECTION_BACKFILL,
    CONVERSATION_PATH,
    REFLECTIONS_PATH,
    E5_EMBED_URL,
)
from utils.logger import warning, log


# In-memory mirror of the on-disk index
_index: list[dict] = []
_index_keys: set[str] = set()
_loaded = False


# ── file helpers ──────────────────────────────────────────────
def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return out


def _load_index() -> None:
    global _loaded
    if _loaded:
        return
    _index.clear()
    _index_keys.clear()
    for e in _read_jsonl(RECALL_INDEX_PATH):
        key = e.get("key")
        emb = e.get("embedding")
        if key and emb:
            _index.append(e)
            _index_keys.add(key)
    _loaded = True


def _append_index(entry: dict) -> None:
    try:
        RECALL_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RECALL_INDEX_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _index.append(entry)
        _index_keys.add(entry["key"])
    except Exception as exc:
        warning(f"semantic_recall/append_index: {type(exc).__name__}: {exc}")


# ── embedding ──────────────────────────────────────────────
async def _embed(text: str, client: httpx.AsyncClient | None) -> list[float] | None:
    text = (text or "").strip()
    if not text:
        return None
    timeout = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=2.0)
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as c:
                resp = await c.post(E5_EMBED_URL, json={"content": text})
        else:
            resp = await client.post(E5_EMBED_URL, json={"content": text}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        # e5 (llama-server) shape: [{"index": 0, "embedding": [[...4096 floats...]]}]
        return data[0]["embedding"][0]
    except Exception as exc:
        warning(f"semantic_recall/embed: {type(exc).__name__}: {exc}")
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ── indexing ──────────────────────────────────────────────
def _pending_items() -> list[dict]:
    """Conversation turns + recent reflections not yet in the index."""
    items: list[dict] = []

    for e in _read_jsonl(CONVERSATION_PATH):
        key = e.get("id")
        text = (e.get("content") or "").strip()
        if not key or key in _index_keys or len(text) < RECALL_MIN_CHARS:
            continue
        items.append({
            "key": key,
            "kind": "turn",
            "ts": e.get("ts", ""),
            "role": e.get("role", ""),
            "text": text,
        })

    reflections = _read_jsonl(REFLECTIONS_PATH)
    if RECALL_REFLECTION_BACKFILL and len(reflections) > RECALL_REFLECTION_BACKFILL:
        reflections = reflections[-RECALL_REFLECTION_BACKFILL:]
    for e in reflections:
        ts = e.get("ts", "")
        text = (e.get("text") or "").strip()
        if not ts or len(text) < RECALL_MIN_CHARS:
            continue
        key = f"refl:{ts}"
        if key in _index_keys:
            continue
        items.append({
            "key": key,
            "kind": "reflection",
            "ts": ts,
            "text": text,
        })

    return items


async def reindex(client: httpx.AsyncClient | None, batch: int | None = None) -> int:
    """Embed a throttled batch of not-yet-indexed turns/reflections.

    Returns the number indexed this pass. Stops early if e5 is unreachable so it
    simply retries on the next heartbeat. Never raises.
    """
    if not RECALL_ENABLED:
        return 0
    try:
        _load_index()
        pending = _pending_items()
        if not pending:
            return 0
        limit = batch if batch is not None else RECALL_INDEX_BATCH
        done = 0
        for item in pending[:limit]:
            emb = await _embed(item["text"], client)
            if emb is None:
                break  # e5 down — bail, retry next pass
            entry = {
                "key": item["key"],
                "kind": item["kind"],
                "ts": item.get("ts", ""),
                "text": item["text"][:1000],
                "embedding": emb,
            }
            if item.get("role"):
                entry["role"] = item["role"]
            _append_index(entry)
            done += 1
            if RECALL_EMBED_SLEEP:
                await asyncio.sleep(RECALL_EMBED_SLEEP)
        if done:
            log("semantic_recall", "indexed", count=done, remaining=len(pending) - done)
        return done
    except Exception as exc:
        warning(f"semantic_recall/reindex: {type(exc).__name__}: {exc}")
        return 0


# ── retrieval ─────────────────────────────────────────────
def _excluded_keys() -> set[str]:
    """Keys already visible to the model, so recall doesn't echo them: the most
    recent conversation messages (live window) + Membrane-recent reflections."""
    excl: set[str] = set()

    convo = _read_jsonl(CONVERSATION_PATH)
    for e in convo[-RECALL_RECENT_EXCLUDE_TURNS:]:
        key = e.get("id")
        if key:
            excl.add(key)

    cutoff = datetime.now() - timedelta(hours=RECALL_RECENT_HOURS)
    for e in _read_jsonl(REFLECTIONS_PATH):
        ts = e.get("ts", "")
        if not ts:
            continue
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if when.tzinfo is not None:
            when = when.replace(tzinfo=None)
        if when >= cutoff:
            excl.add(f"refl:{ts}")

    return excl


def _fmt_date(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%b %d, %Y")
    except Exception:
        return ""


def _format_block(scored: list[tuple[float, dict]]) -> str | None:
    lines = [
        "[RECALLED FROM EARLIER]",
        "Relevant moments from before — older parts of your conversations and your "
        "own past reflections that connect to what's being discussed now. This is "
        "long-term memory, not the present moment; lean on it only where it "
        "genuinely helps.",
        "",
    ]
    for _sim, e in scored:
        when = _fmt_date(e.get("ts", ""))
        when_s = f" ({when})" if when else ""
        text = (e.get("text") or "").strip().replace("\n", " ")
        if e.get("kind") == "reflection":
            lines.append(f"  - Your past reflection{when_s}: {text[:400]}")
        else:
            role = e.get("role", "")
            who = "You said" if role == "assistant" else "Elliot said" if role == "user" else "Earlier"
            lines.append(f"  - {who}{when_s}: {text[:400]}")

    block = "\n".join(lines).strip()
    if len(block) > RECALL_MAX_CHARS:
        block = block[:RECALL_MAX_CHARS].rstrip() + "\n...[truncated]"
    return block if block else None


async def recall(query_text: str, client: httpx.AsyncClient | None) -> str | None:
    """Return a formatted recall block for the current message, or None.

    Never raises — degrades silently to None (no recall) on any failure.
    """
    if not RECALL_ENABLED:
        return None
    try:
        q = (query_text or "").strip()
        if len(q) < RECALL_MIN_CHARS:
            return None
        _load_index()
        if not _index:
            return None

        q_emb = await _embed(q, client)
        if q_emb is None:
            return None

        excluded = _excluded_keys()
        scored: list[tuple[float, dict]] = []
        for e in _index:
            if e.get("key") in excluded:
                continue
            sim = _cosine(q_emb, e.get("embedding") or [])
            if sim >= RECALL_MIN_SIM:
                scored.append((sim, e))

        if not scored:
            return None
        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[:RECALL_TOP_K]
        block = _format_block(top)
        if block:
            log("semantic_recall", "recall",
                hits=len(top), candidates=len(scored), top_sim=round(top[0][0], 3))
        return block
    except Exception as exc:
        warning(f"semantic_recall/recall: {type(exc).__name__}: {exc}")
        return None
