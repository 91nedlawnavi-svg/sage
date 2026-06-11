"""Novelty gate — prevents curiosity topic fixation using e5 embeddings (Phase 2.2).

Maintains an in-memory ring buffer of recent curiosity topics. Each new
candidate is embedded via e5 and checked for cosine similarity against the
buffer. If too similar (circling), the gate rejects it and optionally
forces a divergence seed from a rotating list.

Falls back to lexical overlap when e5 is unreachable. Never raises.
"""

import asyncio
import math
import httpx
from typing import Any

from config.settings import (
    NOVELTY_GATE_ENABLED,
    NOVELTY_WINDOW,
    NOVELTY_SIM_THRESHOLD,
    NOVELTY_MAX_RETRIES,
    CURIOSITY_STREAK_CAP,
    DIVERGENCE_SEEDS,
    E5_EMBED_URL,
)
from utils.logger import info, warning


class NoveltyGate:
    """Ring buffer of recent curiosity topics with e5-based novelty checking.

    One global instance (gate) lives for the process lifetime. The buffer is
    only touched from the heartbeat asyncio task, so no locking is needed.
    """

    def __init__(self) -> None:
        self._buffer: list[tuple[str, list[float] | None]] = []  # (text, embedding)
        self._streak_counter: int = 0
        self._diverge_index: int = 0
        self._accept_count: int = 0

    # ── public queries ──────────────────────────────────────────────

    def recent_themes(self) -> list[str]:
        """Return recent topic texts, newest first (for anti-repeat prompts)."""
        return [t[0] for t in self._buffer[-NOVELTY_WINDOW:]][::-1]

    @property
    def streak(self) -> int:
        return self._streak_counter

    @property
    def accept_count(self) -> int:
        return self._accept_count

    # ── e5 embedding ────────────────────────────────────────────────

    async def embed(self, text: str, client: httpx.AsyncClient | None = None) -> list[float] | None:
        """Embed text via e5 on 127.0.0.1:8081.

        Returns a 4096-d vector or None on any failure (unreachable, bad
        response, etc.).
        """
        if not text.strip():
            return None
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    resp = await c.post(E5_EMBED_URL, json={"content": text.strip()})
            else:
                resp = await client.post(E5_EMBED_URL, json={"content": text.strip()})
            resp.raise_for_status()
            data = resp.json()
            # Expected shape: [{"index":0, "embedding":[[4096 floats]]}]
            return data[0]["embedding"][0]
        except Exception as exc:
            warning(f"novelty_gate/embed: {exc}")
            return None

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    def _max_sim_vs_buffer(self, embedding: list[float]) -> float:
        if not self._buffer or not embedding:
            return 0.0
        return max(self._cosine_sim(embedding, e) for _, e in self._buffer if e)

    @staticmethod
    def _lex_overlap(a: str, b: str) -> float:
        ws_a = set(a.lower().split())
        ws_b = set(b.lower().split())
        if not ws_a or not ws_b:
            return 0.0
        return len(ws_a & ws_b) / max(len(ws_a), len(ws_b))

    # ── core gate ───────────────────────────────────────────────────

    async def evaluate(
        self,
        text: str,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """Evaluate a curiosity candidate and return verdict.

        Returns a dict with:
          action: "accept" | "reject" | "diverge"
          max_sim: float (0–1), highest similarity vs buffer
          final_text: str — as input for accept/reject,
                      the divergence seed for diverge
          embedding: list[float] | None — precomputed if e5 worked

        On "accept": the text was pushed into the ring buffer.
        On "reject": caller should regenerate and re-evaluate.
        On "diverge": streak cap hit; caller MUST use final_text.
        """
        result: dict[str, Any] = {
            "action": "accept",
            "max_sim": 0.0,
            "final_text": text,
            "embedding": None,
        }

        if not NOVELTY_GATE_ENABLED or not text.strip():
            if text.strip():
                self._push(text, None)
            return result

        # 1. Try semantic (e5) check
        embedding = await self.embed(text, client)
        result["embedding"] = embedding

        if embedding is not None:
            max_sim = self._max_sim_vs_buffer(embedding)
            result["max_sim"] = max_sim

            if max_sim < NOVELTY_SIM_THRESHOLD:
                # Genuinely novel — accept
                self._push(text, embedding)
                return result

            # Circling (semantic match)
            self._streak_counter += 1
            info("novelty_gate/reject-semantic",
                 text=trunc(text, 60), max_sim=round(max_sim, 3),
                 streak=self._streak_counter)

            if self._streak_counter >= CURIOSITY_STREAK_CAP:
                return self._force_diverge()

            return {"action": "reject", "max_sim": max_sim,
                    "final_text": text, "embedding": embedding}

        # 2. e5 failed → lexical fallback
        if self._buffer:
            texts = [b[0] for b in self._buffer]
            max_lex = max(self._lex_overlap(text, t) for t in texts)
            result["max_sim"] = max_lex

            if max_lex > 0.7:
                self._streak_counter += 1
                info("novelty_gate/reject-lexical",
                     text=trunc(text, 60), max_lex=round(max_lex, 3),
                     streak=self._streak_counter)

                if self._streak_counter >= CURIOSITY_STREAK_CAP:
                    return self._force_diverge()

                return {"action": "reject", "max_sim": max_lex,
                        "final_text": text, "embedding": None}
            else:
                self._streak_counter = 0

        # Accept via lexical fallback
        self._push(text, None)
        return result

    # ── internal ────────────────────────────────────────────────────

    def push(self, text: str, embedding: list[float] | None) -> None:
        """Add a topic + optional embedding to the ring buffer."""
        self._buffer.append((text, embedding))
        if len(self._buffer) > NOVELTY_WINDOW:
            self._buffer.pop(0)
        self._streak_counter = 0
        self._accept_count += 1

    def _push(self, text: str, embedding: list[float] | None) -> None:
        self.push(text, embedding)

    def _force_diverge(self) -> dict[str, Any]:
        seed = DIVERGENCE_SEEDS[self._diverge_index % len(DIVERGENCE_SEEDS)]
        self._diverge_index += 1
        self._streak_counter = 0
        info("novelty_gate/diverge", seed=seed)
        return {"action": "diverge", "max_sim": 1.0,
                "final_text": seed, "embedding": None}


def trunc(s: str, n: int) -> str:
    return s[:n] + "…" if len(s) > n else s


# Global singleton — lives for the process lifetime
gate = NoveltyGate()
