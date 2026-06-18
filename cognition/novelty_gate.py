"""Novelty gate — prevents curiosity topic fixation using e5 embeddings (Phase 2.2/2.2b).

Maintains an in-memory ring buffer of recent curiosity topics. Each new
candidate is embedded via e5 and checked for cosine similarity against the
buffer. If too similar (circling), the gate rejects it and optionally
forces a divergence seed from a rotating list.

Phase 2.2b adds basin-drift detection: a rolling centroid of recent ACCEPTED
topics. Accepts that stay within the basin (above BASIN_SIM_THRESHOLD) increment
a BASIN-STREAK that is NOT reset by accept — catching slow lateral drift the
reject-streak never catches.

Falls back to lexical overlap when e5 is unreachable. Never raises.
"""

import asyncio
import math
import time
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
    BASIN_WINDOW,
    BASIN_SIM_THRESHOLD,
    BASIN_STREAK_CAP,
    HEARTBEAT_INTERVAL_SECONDS,
    STALL_TICKS,
    REFLECTION_BASIN_SIM_THRESHOLD,
    REFLECTION_BASIN_WINDOW,
    REFLECTION_BASIN_STREAK_CAP,
    REFLECTION_DIVERGE_HOLD,
)
from utils.logger import info, warning


class NoveltyGate:
    """Ring buffer of recent curiosity topics with e5-based novelty checking.

    One global instance (gate) lives for the process lifetime. The buffer is
    only touched from the heartbeat asyncio task, so no locking is needed.
    """

    def __init__(self) -> None:
        self._buffer: list[tuple[str, list[float] | None]] = []  # (text, embedding)
        self._streak_counter: int = 0  # reject-streak: consecutive semantic rejections
        self._diverge_index: int = 0
        self._accept_count: int = 0

        # Phase 2.2b — basin-drift detection
        self._centroid_buffer: list[list[float]] = []  # rolling accepted embeddings
        self._basin_streak: int = 0  # consecutive accepts IN THE BASIN (not reset by accept)
        self._last_novel_accept_time: float = 0.0  # time of last out-of-basin accept

        # fix #5 (Phase 2.2c) — reflection-stream basin detection
        self._reflection_buffer: list[list[float]] = []
        self._reflection_basin_streak: int = 0
        self._reflection_diverge_hold: int = 0   # reflections left to force a seed

    # ── e5 embedding ────────────────────────────────────────────────

    async def embed(self, text: str, client: httpx.AsyncClient | None = None) -> list[float] | None:
        """Embed text via e5 on 127.0.0.1:8081.

        Returns a 1024-d vector or None on any failure (unreachable, bad
        response, etc.). The gate only ever compares these vectors against each
        other (buffer / centroids), so it applies the e5 "query: " prefix
        uniformly — consistency matters here, not query/passage roles.
        """
        if not text.strip():
            return None
        payload = "query: " + text.strip()
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    resp = await c.post(E5_EMBED_URL, json={"content": payload})
            else:
                resp = await client.post(
                    E5_EMBED_URL,
                    json={"content": payload},
                    timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=2.0),
                )
            resp.raise_for_status()
            data = resp.json()
            # Expected shape: [{"index":0, "embedding":[[1024 floats]]}]
            return data[0]["embedding"][0]
        except Exception as exc:
            warning(f"novelty_gate/embed: {type(exc).__name__}: {exc}")
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

    @staticmethod
    def _lex_basin_check(text: str, buffer: list[tuple[str, list[float] | None]]) -> float | None:
        """Jaccard of candidate vs union of all buffer texts — lexical proxy for e5-down."""
        if not buffer:
            return None
        union_words: set[str] = set()
        for t, _ in buffer:
            union_words |= set(t.lower().split())
        candidate_words = set(text.lower().split())
        if not union_words or not candidate_words:
            return None
        return len(candidate_words & union_words) / len(candidate_words | union_words)

    # ── centroid / basin-drift (Phase 2.2b) ───────────────────────

    def _compute_centroid(self) -> list[float] | None:
        """Mean of _centroid_buffer, renormalized. Pure-Python fallback if numpy absent."""
        if not self._centroid_buffer:
            return None
        dim = len(self._centroid_buffer[0])
        try:
            import numpy as np  # type: ignore[import-untyped]
            arr = np.array(self._centroid_buffer)
            mean = arr.mean(axis=0)
            norm = np.linalg.norm(mean)
            return (mean / norm).tolist() if norm else None
        except ImportError:
            # Pure-Python fallback
            mean = [0.0] * dim
            for vec in self._centroid_buffer:
                for i in range(dim):
                    mean[i] += vec[i] / len(self._centroid_buffer)
            norm = math.sqrt(sum(x * x for x in mean))
            return [x / norm for x in mean] if norm else None

    def _centroid_sim(self, embedding: list[float]) -> float | None:
        """Cosine similarity of *embedding* against the current centroid."""
        centroid = self._compute_centroid()
        if centroid is None:
            return None
        return self._cosine_sim(embedding, centroid)

    def _update_centroid(self, embedding: list[float]) -> None:
        """Push embedding into the rolling centroid buffer."""
        self._centroid_buffer.append(embedding)
        if len(self._centroid_buffer) > BASIN_WINDOW:
            self._centroid_buffer.pop(0)

    def _age_centroid_buffer(self) -> None:
        """Drop to last 2-3 entries so a new seed isn't judged against the saturated basin."""
        if len(self._centroid_buffer) > 3:
            self._centroid_buffer = self._centroid_buffer[-3:]

    # ── divergence seeds ─────────────────────────────────────────

    def _next_divergence_seed(self) -> str:
        """Consume and return the next rotating divergence seed."""
        seed = DIVERGENCE_SEEDS[self._diverge_index % len(DIVERGENCE_SEEDS)]
        self._diverge_index += 1
        return seed

    # ── public queries (including Phase 2.2b stall) ─────────────

    def recent_themes(self) -> list[str]:
        """Return recent topic texts, newest first (for anti-repeat prompts)."""
        return [t[0] for t in self._buffer[-NOVELTY_WINDOW:]][::-1]

    @property
    def streak(self) -> int:
        return self._streak_counter

    @property
    def accept_count(self) -> int:
        return self._accept_count

    @property
    def basin_streak(self) -> int:
        """Consecutive accepts still inside the basin."""
        return self._basin_streak

    @property
    def stalled(self) -> bool:
        """True if no genuinely-novel (out-of-basin) topic accepted in the last STALL_TICKS heartbeats."""
        if self._last_novel_accept_time == 0.0:
            return False
        elapsed = time.time() - self._last_novel_accept_time
        ticks = int(elapsed / HEARTBEAT_INTERVAL_SECONDS)
        return ticks >= STALL_TICKS

    @property
    def ticks_since_novel(self) -> int | None:
        """Approximate heartbeat ticks since last out-of-basin accept."""
        if self._last_novel_accept_time == 0.0:
            return None
        elapsed = time.time() - self._last_novel_accept_time
        return int(elapsed / HEARTBEAT_INTERVAL_SECONDS)

    def consume_divergence_seed(self) -> str:
        """Public: consume the next rotating divergence seed. Used by stall / reflection path."""
        return self._next_divergence_seed()

    # ── reflection-stream basin (fix #5 / Phase 2.2c) ──────────────

    @property
    def reflection_diverge_pending(self) -> bool:
        """True while a reflection-basin break is still forcing seeds."""
        return self._reflection_diverge_hold > 0

    def consume_reflection_hold(self) -> None:
        """Decrement the forced-seed hold counter (once per forced beat)."""
        if self._reflection_diverge_hold > 0:
            self._reflection_diverge_hold -= 1

    def _reflection_centroid(self) -> list[float] | None:
        if not self._reflection_buffer:
            return None
        dim = len(self._reflection_buffer[0])
        m = [0.0] * dim
        for v in self._reflection_buffer:
            for i in range(dim):
                m[i] += v[i] / len(self._reflection_buffer)
        nrm = math.sqrt(sum(x * x for x in m))
        return [x / nrm for x in m] if nrm else None

    async def track_reflection(self, text: str,
                               client: httpx.AsyncClient | None = None) -> None:
        """Embed an emitted reflection and test it against the rolling reflection
        centroid. On a confirmed basin lock, arm a STRONG break: hold a forced
        divergence seed for REFLECTION_DIVERGE_HOLD reflections and wipe the
        buffer so the new topic re-anchors. Never raises.
        """
        try:
            emb = await self.embed(text, client)
            if emb is None:
                return
            centroid = self._reflection_centroid()
            sim = self._cosine_sim(emb, centroid) if centroid else None
            if sim is not None and sim >= REFLECTION_BASIN_SIM_THRESHOLD:
                self._reflection_basin_streak += 1
                info("novelty_gate/reflection-basin-streak",
                     sim=round(sim, 3), streak=self._reflection_basin_streak)
                if self._reflection_basin_streak >= REFLECTION_BASIN_STREAK_CAP:
                    self._reflection_diverge_hold = REFLECTION_DIVERGE_HOLD
                    self._reflection_basin_streak = 0
                    self._reflection_buffer = []   # full reset: strong break
                    info("novelty_gate/reflection-basin-diverge",
                         sim=round(sim, 3), hold=REFLECTION_DIVERGE_HOLD)
                    return
            else:
                self._reflection_basin_streak = 0
            self._reflection_buffer.append(emb)
            if len(self._reflection_buffer) > REFLECTION_BASIN_WINDOW:
                self._reflection_buffer.pop(0)
        except Exception as exc:
            warning(f"novelty_gate/track_reflection: {exc}")

    # ── e5 embedding ────────────────────────────────────────────────

    async def evaluate(
        self,
        text: str,
        client: httpx.AsyncClient | None = None,
        retry: bool = False,
    ) -> dict[str, Any]:
        """Evaluate a curiosity candidate and return verdict.

        When *retry* is True (Phase 2.2b retry path), the gate skips the
        normal rejection -> slow-lateral-drift cycle and instead goes
        straight to divergence if the candidate is still circling OR is
        still within the basin (no accepting barely-passing sidesteps).

        Returns a dict with:
          action: "accept" | "reject" | "diverge"
          max_sim: float (0–1), highest similarity vs buffer
          final_text: str — as input for accept/reject,
                      the divergence seed for diverge
          embedding: list[float] | None — precomputed if e5 worked
          divergence_seed: str | None — present on "reject"; caller
                           should steer the retry toward this seed

        On "accept": the text was pushed into the ring buffer.
        On "reject": caller should regenerate using divergence_seed.
        On "diverge": streak cap hit; caller MUST use final_text.
        """
        result: dict[str, Any] = {
            "action": "accept",
            "max_sim": 0.0,
            "final_text": text,
            "embedding": None,
            "divergence_seed": None,
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

            # ── retry path (Phase 2.2b): no more accepting sidesteps ──
            if retry:
                if max_sim >= NOVELTY_SIM_THRESHOLD:
                    # Still circling after retry — diverge immediately
                    return self._force_diverge()
                sim_centroid = self._centroid_sim(embedding)
                if sim_centroid is not None and sim_centroid >= BASIN_SIM_THRESHOLD:
                    # Still lateral drifting in basin — diverge, don't accept
                    return self._force_basin_diverge()
                # Genuinely escaped — accept
                self._push(text, embedding)
                return result

            # ── first-try path ────────────────────────────────────────
            if max_sim < NOVELTY_SIM_THRESHOLD:
                # Novelty check passed — now check basin before accepting
                sim_centroid = self._centroid_sim(embedding)
                if sim_centroid is not None and sim_centroid >= BASIN_SIM_THRESHOLD:
                    # Still within the basin — drift detected
                    self._basin_streak += 1
                    info("novelty_gate/basin-streak",
                         text=trunc(text, 60),
                         sim_to_centroid=round(sim_centroid, 3),
                         basin_streak=self._basin_streak)

                    if self._basin_streak >= BASIN_STREAK_CAP:
                        return self._force_basin_diverge()

                    # Accept it (novel enough) but basin_streak stays incremented
                else:
                    # Genuine escape from the basin
                    self._basin_streak = 0
                    self._last_novel_accept_time = time.time()

                self._push(text, embedding)
                return result

            # Circling (semantic match) — first-try rejection
            self._streak_counter += 1
            seed = self._next_divergence_seed()
            info("novelty_gate/reject-semantic",
                 text=trunc(text, 60), max_sim=round(max_sim, 3),
                 streak=self._streak_counter)

            if self._streak_counter >= CURIOSITY_STREAK_CAP:
                return self._force_diverge()

            return {"action": "reject", "max_sim": max_sim,
                    "final_text": text, "embedding": embedding,
                    "divergence_seed": seed}

        # ── 2. e5 failed → lexical fallback ──────────────────────────
        if self._buffer:
            texts = [b[0] for b in self._buffer]
            max_lex = max(self._lex_overlap(text, t) for t in texts)
            result["max_sim"] = max_lex

            if max_lex > 0.7:
                # Retry path for lexical fallback
                if retry:
                    return self._force_diverge()

                self._streak_counter += 1
                seed = self._next_divergence_seed()
                info("novelty_gate/reject-lexical",
                     text=trunc(text, 60), max_lex=round(max_lex, 3),
                     streak=self._streak_counter)

                if self._streak_counter >= CURIOSITY_STREAK_CAP:
                    return self._force_diverge()

                return {"action": "reject", "max_sim": max_lex,
                        "final_text": text, "embedding": None,
                        "divergence_seed": seed}
            else:
                self._streak_counter = 0

        # Accept via lexical fallback — check lexical basin if e5 is down
        if embedding is None:
            jaccard = self._lex_basin_check(text, self._buffer)
            if jaccard is not None and jaccard >= 0.50:
                if retry:
                    return self._force_basin_diverge()
                self._basin_streak += 1
                info("novelty_gate/basin-streak-lexical",
                     text=trunc(text, 60), jaccard=round(jaccard, 3),
                     basin_streak=self._basin_streak)
                if self._basin_streak >= BASIN_STREAK_CAP:
                    return self._force_basin_diverge()
            else:
                self._basin_streak = 0 if jaccard is None else self._basin_streak
                if jaccard is not None and jaccard < 0.50:
                    self._basin_streak = 0
                    self._last_novel_accept_time = time.time()

        self._push(text, None)
        return result

    # ── internal ────────────────────────────────────────────────────

    def push(self, text: str, embedding: list[float] | None) -> None:
        """Add a topic + optional embedding to the ring buffer AND centroid buffer."""
        self._buffer.append((text, embedding))
        if len(self._buffer) > NOVELTY_WINDOW:
            self._buffer.pop(0)

        # Phase 2.2b: update centroid buffer
        if embedding is not None:
            self._update_centroid(embedding)

        self._streak_counter = 0  # reject-streak resets on genuine accept
        self._accept_count += 1

    def _push(self, text: str, embedding: list[float] | None) -> None:
        self.push(text, embedding)

    def _force_diverge(self) -> dict[str, Any]:
        """Force divergence from reject-streak cap (CURIOSITY_STREAK_CAP)."""
        seed = self._next_divergence_seed()
        self._streak_counter = 0
        info("novelty_gate/diverge", seed=seed)
        return {"action": "diverge", "max_sim": 1.0,
                "final_text": seed, "embedding": None}

    def _force_basin_diverge(self) -> dict[str, Any]:
        """Force divergence from basin-drift cap (BASIN_STREAK_CAP).

        Ages the centroid buffer so the seed topic isn't instantly rejected
        against the saturated basin.
        """
        seed = self._next_divergence_seed()
        self._age_centroid_buffer()
        self._basin_streak = 0
        self._streak_counter = 0
        info("novelty_gate/diverge-basin", seed=seed,
             buffer_age=len(self._centroid_buffer))
        return {"action": "diverge", "max_sim": 1.0,
                "final_text": seed, "embedding": None}


def trunc(s: str, n: int) -> str:
    return s[:n] + "…" if len(s) > n else s


# Global singleton — lives for the process lifetime
gate = NoveltyGate()
