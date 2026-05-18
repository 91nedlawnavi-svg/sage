"""
memory/embeddings.py — Local embedding via llama.cpp (port 8082)

Wraps the BGE-M3 embedding endpoint with:
  - in-memory LRU cache (avoid re-embedding identical text)
  - filesystem cache for persistence across restarts
  - cosine similarity utility
"""

import asyncio
import hashlib
import json
import math
from pathlib import Path
from typing import Optional

import httpx

from config import EMBED_API_URL, EMBED_CACHE_MAX, EMBED_PREFIX, EMBEDDINGS_DIR


# ── In-memory LRU cache ──────────────────────────────────────────────

_cache: dict[str, list[float]] = {}
_cache_lock = asyncio.Lock()


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _cache_get(key: str) -> Optional[list[float]]:
    async with _cache_lock:
        return _cache.get(key)


async def _cache_set(key: str, vec: list[float]) -> None:
    async with _cache_lock:
        if len(_cache) >= EMBED_CACHE_MAX:
            # Evict oldest 25%
            victims = list(_cache.keys())[: EMBED_CACHE_MAX // 4]
            for v in victims:
                del _cache[v]
        _cache[key] = vec


# ── Filesystem cache ─────────────────────────────────────────────────

def _fs_path(key: str) -> Path:
    return EMBEDDINGS_DIR / f"{key[:16]}.json"


async def _fs_get(key: str) -> Optional[list[float]]:
    path = _fs_path(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("vec")
    except Exception:
        return None


async def _fs_set(key: str, vec: list[float]) -> None:
    path = _fs_path(key)
    try:
        path.write_text(json.dumps({"vec": vec}), encoding="utf-8")
    except Exception:
        pass  # Cache write failure is non-fatal


# ── Public API ───────────────────────────────────────────────────────

async def get_embedding(
    text: str, client: httpx.AsyncClient
) -> Optional[list[float]]:
    """
    Return embedding vector for text.
    Check memory cache → filesystem cache → llama.cpp endpoint.
    """
    key = _key(text)

    # 1. Memory cache
    hit = await _cache_get(key)
    if hit is not None:
        return hit

    # 2. Filesystem cache
    hit = await _fs_get(key)
    if hit is not None:
        await _cache_set(key, hit)
        return hit

    # 3. Call llama.cpp
    try:
        resp = await client.post(
            EMBED_API_URL,
            json={"input": EMBED_PREFIX + text},
            timeout=30.0,
        )
        resp.raise_for_status()
        vec = resp.json().get("data", [{}])[0].get("embedding")
        if vec:
            await _cache_set(key, vec)
            await _fs_set(key, vec)
        return vec
    except Exception as e:
        from utils.logger import log
        log("memory", "embedding_error", error=str(e))
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Standard cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0
