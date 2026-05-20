"""
memory/emotional.py — Emotional memory layer

Stores interpretations of emotional patterns, not events:
  - recurring emotional themes
  - motivational patterns
  - how the user relates to people / situations over time

Each file represents one ongoing emotional theme or pattern.
Files are updated (merged), not duplicated.

Format per file:
  [theme_name]
  [last_updated]
  [interpretation text]
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from config import EMOTIONAL_DIR, RETRIEVAL_THRESHOLD
from memory.embeddings import cosine_similarity, get_embedding
from memory.storage import (
    ensure_dirs,
    list_memory_files,
    read_memory_entry,
    safe_stem,
    write_text,
)


def _theme_path(theme_name: str) -> Path:
    """Derive the file path for a named emotional theme."""
    return EMOTIONAL_DIR / f"{safe_stem(theme_name)}.txt"


async def write_emotional_theme(theme_name: str, interpretation: str) -> Path:
    """
    Write or overwrite an emotional theme file.
    Called by the memory writer after it produces a new interpretation.
    """
    ensure_dirs(EMOTIONAL_DIR)
    path = _theme_path(theme_name)
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = f"[{theme_name}]\n[updated: {ts}]\n{interpretation.strip()}\n"
    await write_text(path, content)
    return path


async def load_all_themes() -> list[tuple[str, str]]:
    """
    Load all emotional themes as (name, content) pairs.
    Used by retrieval and reflection.
    """
    files = await list_memory_files(EMOTIONAL_DIR)
    result = []
    for f in files:
        content = await read_memory_entry(f)
        if content:
            result.append((f.stem, content))
    return result


async def retrieve_relevant_themes(
    query: str,
    client: httpx.AsyncClient,
    top_k: int = 3,
    threshold: float = RETRIEVAL_THRESHOLD,
) -> list[tuple[str, str]]:
    """
    Return the top_k emotional themes most relevant to query.

    Uses the same embedding + cosine-similarity approach as
    memory/retrieval.py — no new infrastructure.

    Falls back to [] if the embedding service is unavailable or
    no themes exist / pass threshold.
    """
    themes = await load_all_themes()
    if not themes:
        return []

    query_vec = await get_embedding(query, client)
    if query_vec is None:
        # Embedding service down — fail open with no themes rather than
        # falling back to the full dump (avoids the original problem).
        return []

    async def _score(name: str, content: str):
        vec = await get_embedding(content[:600], client)
        if vec is None:
            return None
        return (cosine_similarity(query_vec, vec), name, content)

    results = await asyncio.gather(*[_score(n, c) for n, c in themes])

    scored = [r for r in results if r is not None and r[0] >= threshold]
    scored.sort(key=lambda x: x[0], reverse=True)

    return [(name, content) for _, name, content in scored[:top_k]]


async def load_theme(theme_name: str) -> Optional[str]:
    """Load a single theme by name."""
    path = _theme_path(theme_name)
    if not path.exists():
        return None
    return await read_memory_entry(path)


async def list_theme_names() -> list[str]:
    """Return the stem names of all emotional theme files."""
    files = await list_memory_files(EMOTIONAL_DIR)
    return [f.stem for f in files]
