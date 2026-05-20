"""
memory/retrieval.py — Semantic memory retrieval

Queries all memory layers (episodic, emotional, library) using
local embeddings and returns the most relevant chunks.

Design principles:
  - Never stuffs the entire memory into context
  - Returns only chunks above a similarity threshold
  - Caps total injected memory at TOP_K_MEMORIES
  - Runs async, non-blocking
"""

import asyncio
from pathlib import Path
from typing import Optional

import httpx

from config import TOP_K_MEMORIES, RETRIEVAL_THRESHOLD, LIBRARY_CATS, LIBRARY_DIR
from memory.embeddings import get_embedding, cosine_similarity
from memory.episodic import load_all_episodes, load_all_reflections
from memory.emotional import load_all_themes
from memory.storage import list_memory_files, read_memory_entry
from utils.logger import log


async def _score_chunk(
    query_vec: list[float],
    label: str,
    content: str,
    client: httpx.AsyncClient,
) -> Optional[tuple[float, str, str]]:
    """
    Embed one memory chunk and return (score, label, content).
    Returns None on failure.
    """
    try:
        # Embed only the first 600 chars to keep it fast
        vec = await get_embedding(content[:600], client)
        if vec is None:
            return None
        score = cosine_similarity(query_vec, vec)
        return (score, label, content)
    except Exception as e:
        log("retrieval", "score_error", label=label, error=str(e))
        return None


async def retrieve_relevant_memories(
    query: str,
    client: httpx.AsyncClient,
    top_k: int = TOP_K_MEMORIES,
    threshold: float = RETRIEVAL_THRESHOLD,
) -> str:
    """
    Search episodic, emotional, and library memory for the query.
    Returns a formatted string ready for prompt injection.
    Returns '' if nothing relevant found.
    """
    query_vec = await get_embedding(query, client)
    if query_vec is None:
        return ""

    # Gather all candidate chunks
    candidates: list[tuple[str, str]] = []  # (label, content)

    # Retrieval caps: prevent unbounded growth in candidate pool.
    # Files are sorted ascending by timestamp name; slicing [-N:] keeps the
    # most recent N, which have the highest signal for live conversation.
    _EPISODIC_CAP    = 200
    _REFLECTION_CAP  = 90

    # 1. Episodic
    episodes = await load_all_episodes()
    for path, content in episodes[-_EPISODIC_CAP:]:
        candidates.append((f"episodic/{path.stem}", content))

    # 2. Emotional themes
    themes = await load_all_themes()
    for name, content in themes:
        candidates.append((f"emotional/{name}", content))

    # 3. Legacy library (people, places, events, topics, self)
    for cat in LIBRARY_CATS:
        cat_dir = LIBRARY_DIR / cat
        if not cat_dir.exists():
            continue
        for f in cat_dir.glob("*.txt"):
            content = await read_memory_entry(f)
            if content:
                candidates.append((f"library/{cat}/{f.stem}", content))

    # 4. Reflections — synthesised autobiographical abstractions
    reflections = await load_all_reflections()
    for path, content in reflections[-_REFLECTION_CAP:]:
        candidates.append((f"reflection/{path.stem}", content))

    if not candidates:
        return ""

    # Score all candidates concurrently
    tasks = [
        _score_chunk(query_vec, label, content, client)
        for label, content in candidates
    ]
    results = await asyncio.gather(*tasks)

    # Filter, sort, cap
    scored = [r for r in results if r is not None and r[0] >= threshold]
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    if not top:
        log("retrieval", "miss", candidates=len(candidates), query_len=len(query))
        return ""

    log("retrieval", "hit",
        candidates=len(candidates),
        above_threshold=len(scored),
        returned=len(top),
        top_score=round(top[0][0], 3) if top else 0,
    )

    parts = []
    for score, label, content in top:
        parts.append(f"[{label}]\n{content.strip()}")

    return "\n\n".join(parts)
