"""Knowledge recall — Phase 4 Layer 2, fact-embedding cache.

Maintains a persisted append-only index of reconciled fact embeddings
at ``FACT_INDEX_PATH``, used by the chat path to admit semantically-
relevant facts that have no lexical overlap with the user's message.

All embedding happens OFF the chat path, driven by the heartbeat.
The chat path only reads the cached vectors — never calls embed.

Contracts mirrored from semantic_recall:
  - Never raises (degrades silently to empty / 0).
  - Gate-aware: reindex_facts is a no-op when KNOWLEDGE_ENABLED is off.
  - Non-blocking off the chat path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

from config.settings import (
    KNOWLEDGE_ENABLED,
    FACT_INDEX_PATH,
    FACT_INDEX_BATCH,
    RECALL_EMBED_SLEEP,
    RECALL_INDEX_READ_TIMEOUT,
)
from memory.semantic_recall import _embed, _cosine, _read_jsonl
from cognition.knowledge_reconcile import reconcile_notebook
from utils.logger import warning, log


# ── fact text rendering (for embedding + hashing) ─────────────────────

def _fact_text(rel: dict, entities: dict) -> str:
    """Render a relation into plain text for the embedding model.

    Uses entity names where available (resolved from the reconciled view),
    so the passage embedding captures the *displayed* meaning of the fact,
    not the raw internal id slug.
    """
    subject = (
        entities.get(rel.get("subject_id", ""), {}).get("name", "")
        or rel.get("subject_id", "")
    )
    predicate = (rel.get("predicate") or "").replace("_", " ")
    obj = rel.get("object", {})
    if obj.get("kind") == "entity":
        obj_text = (
            entities.get(obj.get("value", ""), {}).get("name", "")
            or obj.get("value", "")
        )
    else:
        obj_text = str(obj.get("value", ""))
    return f"{subject} {predicate} {obj_text}".strip()


def _text_hash(rel: dict, entities: dict) -> str:
    """Deterministic SHA1 of the lowercased rendered fact text."""
    text = _fact_text(rel, entities).lower().strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ── cache loading (chat path, read-only, never embeds) ────────────────

def load_fact_vectors() -> dict[str, list[float]]:
    """Load the fact-embedding cache as ``{relation_id: embedding}``.

    Returns an empty dict when the index file is missing, corrupt, or
    when KNOWLEDGE_ENABLED is off — so the caller always sees a
    gracefully-degraded (empty) fallback.
    """
    if not KNOWLEDGE_ENABLED:
        return {}
    if not FACT_INDEX_PATH.exists():
        return {}
    out: dict[str, list[float]] = {}
    for e in _read_jsonl(FACT_INDEX_PATH):
        key = e.get("key")
        emb = e.get("embedding")
        if key and emb:
            out[key] = emb
    return out


# ── index persistence (off the chat path) ─────────────────────────────

def _append_index(entry: dict) -> None:
    """Append one entry to the fact-embedding index (append-only jsonl)."""
    try:
        FACT_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FACT_INDEX_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        warning(f"knowledge_recall/append_index: {type(exc).__name__}: {exc}")


async def reindex_facts(client, *, batch: int | None = None) -> int:
    """Index reconciled facts whose ``(key, text_hash)`` aren't cached yet.

    Only embeds changed or missing facts:
      - If a relation's *key* is not in the cache → embed it.
      - If a key exists but *text_hash* differs → re-embed (fact changed).
      - If a key exists with the same hash → skip (already up to date).

    Returns the number of facts indexed this pass, or 0 on any failure.
    Never raises.  Gate-aware (no-op when KNOWLEDGE_ENABLED is off).
    """
    if not KNOWLEDGE_ENABLED:
        return 0
    try:
        view = reconcile_notebook("relational")
        entities = view.get("entities", {})
        relations = view.get("relations", {})
        if not relations:
            return 0

        # Load existing cache: key -> text_hash
        cache: dict[str, str] = {}
        for e in _read_jsonl(FACT_INDEX_PATH):
            key = e.get("key")
            h = e.get("text_hash")
            if key and h:
                cache[key] = h

        limit = batch if batch is not None else FACT_INDEX_BATCH
        done = 0
        for rid, rel in relations.items():
            if done >= limit:
                break
            h = _text_hash(rel, entities)
            if cache.get(rid) == h:
                continue  # unchanged
            text = _fact_text(rel, entities)
            if not text:
                continue
            emb = await _embed(
                text,
                client,
                read_timeout=RECALL_INDEX_READ_TIMEOUT,
                prefix="passage: ",
            )
            if emb is None:
                # Embedder unavailable — skip this fact, try the next.
                # A full pass can thus make partial progress.
                continue
            _append_index({"key": rid, "text_hash": h, "embedding": emb})
            done += 1
            if RECALL_EMBED_SLEEP:
                await asyncio.sleep(RECALL_EMBED_SLEEP)

        if done:
            log("knowledge_recall", "indexed", count=done)
        return done
    except Exception as exc:
        warning(f"knowledge_recall/reindex: {type(exc).__name__}: {exc}")
        return 0
