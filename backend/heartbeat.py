import asyncio
import time
from datetime import datetime
from config.settings import (
    HEARTBEAT_INTERVAL_SECONDS,
    REFLECTION_MIN_IDLE_SECONDS,
    REFLECTION_COOLDOWN_SECONDS,
    WEB_SEARCH_ENABLED,
    AUTONOMOUS_SEARCH_COOLDOWN_SECONDS,
    AUTONOMOUS_SEARCH_MAX_PER_DAY,
    NOVELTY_GATE_ENABLED,
    NOVELTY_MAX_RETRIES,
)
from cognition.reflection import run_reflection
from cognition.web_search import search
from cognition.curiosity import extract_query
from cognition.novelty_gate import gate as novelty_gate
from memory.reflection_log import append_reflection
from memory.findings_log import append_finding
from backend.session import session
from utils.logger import info, warning, log


class Heartbeat:
    """The autonomous pulse — runs reflection when left alone."""

    def __init__(self, http_client):
        self._client = http_client
        self._task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()
        self._last_reflection_ts: float = 0.0
        self._last_beat_ts: float = 0.0
        self._reflecting = False

        # Search tracking
        self._last_search_ts: float = 0.0
        self._searches_today: int = 0
        self._search_day: str = datetime.now().date().isoformat()

    @property
    def last_beat_ts(self) -> float:
        return self._last_beat_ts

    @property
    def last_reflection_ts(self) -> float:
        return self._last_reflection_ts

    @property
    def last_search_ts(self) -> float:
        return self._last_search_ts

    @property
    def searches_today(self) -> int:
        return self._searches_today

    @property
    def reflecting(self) -> bool:
        return self._reflecting

    def start(self):
        """Launch the background heartbeat task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        info("Heartbeat started")

    def stop(self):
        """Cancel the background task cleanly."""
        self._running = False
        if self._task:
            self._task.cancel()
            info("Heartbeat stopped")

    def _check_day_rollover(self):
        """Reset daily search counter if date changed."""
        today = datetime.now().date().isoformat()
        if today != self._search_day:
            self._search_day = today
            self._searches_today = 0

    async def _run_loop(self):
        """Main heartbeat loop — runs every HEARTBEAT_INTERVAL_SECONDS."""
        while self._running:
            self._last_beat_ts = time.time()
            try:
                await self._maybe_reflect()
            except Exception as e:
                # Loop must never die on a single bad beat
                warning(f"Heartbeat beat error: {e}")
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    async def _maybe_reflect(self):
        """Check conditions and run reflection if appropriate."""
        # Already reflecting? Skip this beat
        if self._lock.locked():
            return

        # Idle gate: only reflect when actually left alone
        idle = session.idle_seconds()
        if idle < REFLECTION_MIN_IDLE_SECONDS:
            return

        # Cooldown gate: respect minimum gap between reflections
        now = time.time()
        if (now - self._last_reflection_ts) < REFLECTION_COOLDOWN_SECONDS:
            return

        # All gates passed — run reflection under lock
        async with self._lock:
            self._reflecting = True
            try:
                text = await run_reflection(self._client)
                if text:
                    append_reflection(text, idle)
                    self._last_reflection_ts = time.time()
                    preview = text[:80]
                    log("heartbeat", "reflection", preview=preview, chars=len(text), idle_seconds=round(idle, 1))

                    # After logging reflection, maybe trigger a search
                    await self._maybe_search(text)
            except Exception as e:
                warning(f"Reflection failed: {e}")
            finally:
                self._reflecting = False

    async def _maybe_search(self, reflection_text: str):
        """Check conditions and run web search if appropriate."""
        if not WEB_SEARCH_ENABLED:
            return

        self._check_day_rollover()

        # Daily cap
        if self._searches_today >= AUTONOMOUS_SEARCH_MAX_PER_DAY:
            return

        # Cooldown gate
        now = time.time()
        if (now - self._last_search_ts) < AUTONOMOUS_SEARCH_COOLDOWN_SECONDS:
            return

        # Extract query from reflection
        query = await extract_query(reflection_text, self._client)
        if not query:
            return

        # ── Novelty gate (Phase 2.2) ──────────────────────────────
        result = await novelty_gate.evaluate(query, self._client)

        if result["action"] == "reject" and NOVELTY_MAX_RETRIES > 0:
            # Try once more with anti-repeat context
            themes = novelty_gate.recent_themes()
            query = await extract_query(reflection_text, self._client,
                                        anti_repeat=themes)
            if query:
                result = await novelty_gate.evaluate(query, self._client)

        if result["action"] == "diverge":
            # Streak exhausted: force a divergence seed as the query
            divergence_text = result["final_text"]
            # Embed and push the divergence seed
            embedding = result.get("embedding")
            if embedding is None:
                embedding = await novelty_gate.embed(divergence_text, self._client)
            # Push to ring buffer so it counts as a topic
            novelty_gate.push(divergence_text, embedding)
            log("novelty_gate", "divergence-issued", query=divergence_text[:80])
            return  # Don't actually search — the divergence is shown, not searched

        if result["action"] == "reject":
            # Still circling after retry — skip this beat
            log("novelty_gate", "skip-search", query=query[:80])
            return

        # ── action == "accept" — proceed to search ────────────────

        # Search (never raises, returns [] on failure)
        try:
            results = search(query)
        except Exception:
            results = []

        # Log finding (even if empty results)
        append_finding(query, results)
        self._last_search_ts = time.time()
        self._searches_today += 1

        log("heartbeat", "search", query=query, n=len(results))