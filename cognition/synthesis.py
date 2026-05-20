"""
cognition/synthesis.py — Memory synthesis

Handles:
  1. Episodic extraction from a conversation digest
  2. Combined reflection generation (episodic + emotional → reflection)

Called by the reflection daemon.
"""

import json
from datetime import datetime
from pathlib import Path

import httpx

from config import (
    NVIDIA_API_KEY,
    NVIDIA_API_URL,
    REFLECTION_MODEL,
    REFLECTIONS_DIR,
)
from memory.episodic import load_recent_episodes, write_episode
from memory.emotional import retrieve_relevant_themes
from memory.storage import ensure_dirs, ts_filename, write_memory_entry
from models.inference import mem_complete
from models.prompts import (
    EPISODIC_SYSTEM,
    REFLECTION_SYSTEM,
    episodic_prompt,
    reflection_prompt,
)
from utils.logger import log

_REFLECTION_SYSTEM = (
    "You synthesize recent experiences and emotional patterns into a single, "
    "concise internal reflection. Write 2–4 sentences. Be psychologically "
    "meaningful and emotionally intelligent. Abstract over specifics. "
    "No roleplay, no persona, no direct address. Plain prose only."
)


async def extract_episode(
    conversation_digest: str,
    client: httpx.AsyncClient,
) -> bool:
    """
    Generate and persist one episodic memory from a conversation digest.
    Returns True if an episode was written, False if skipped.
    """
    raw = await mem_complete(
        system=EPISODIC_SYSTEM,
        user=episodic_prompt(conversation_digest),
        client=client,
        max_tokens=300,
    )

    if not raw or raw.strip().upper() == "SKIP":
        log("cognition", "episodic_skipped")
        return False

    # Derive a short label from the first few words
    words = raw.strip().split()[:4]
    label = "_".join(w.lower() for w in words if w.isalpha())[:32]

    await write_episode(summary=raw.strip(), label=label)
    log("cognition", "episode_written", label=label)
    return True


async def generate_reflection(client: httpx.AsyncClient) -> bool:
    """
    Generate a reflection from recent episodic + semantically relevant emotional memory.
    Persists to data/reflections/.
    Returns True if a reflection was written.
    """
    ensure_dirs(REFLECTIONS_DIR)

    # Gather recent episodic entries
    recent_episodes = await load_recent_episodes(n=5)
    episodic_text = "\n\n".join(recent_episodes) if recent_episodes else ""

    # Retrieve only the emotional themes most relevant to the recent episodes.
    # Using episodic_text as the query anchors retrieval to what actually
    # happened, rather than polling all themes indiscriminately.
    query = episodic_text if episodic_text else ""
    if query:
        themes = await retrieve_relevant_themes(query, client, top_k=3)
    else:
        themes = []
    emotional_text = "\n\n".join(content for _, content in themes) if themes else ""

    if not episodic_text and not emotional_text:
        log("cognition", "reflection_skipped", reason="no_material")
        return False

    # ── NVIDIA Mistral Small 4 reflection call ────────────────────────
    raw = None
    try:
        resp = await client.post(
            NVIDIA_API_URL,
            headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
            json={
                "model": REFLECTION_MODEL,
                "messages": [
                    {"role": "system", "content": _REFLECTION_SYSTEM},
                    {"role": "user",   "content": reflection_prompt(episodic_text, emotional_text)},
                ],
                "stream": False,
                "temperature": 0.7,
                "max_tokens": 180,
            },
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0),
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log("cognition", "reflection_error", error=str(e))
        return False
    # ─────────────────────────────────────────────────────────────────

    if not raw:
        return False

    ts   = datetime.now().strftime("%Y-%m-%d %H:%M")
    stem = ts_filename("reflection_")
    content = f"[{ts}]\n{raw}\n"
    await write_memory_entry(REFLECTIONS_DIR, stem, content)
    log("cognition", "reflection_written", stem=stem)
    return True
