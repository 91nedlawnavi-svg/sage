import asyncio
import time
from config.settings import (
    HEARTBEAT_INTERVAL_SECONDS,
    REFLECTION_MIN_IDLE_SECONDS,
    REFLECTION_COOLDOWN_SECONDS,
)
from cognition.reflection import run_reflection
from memory.reflection_log import append_reflection
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

    @property
    def last_beat_ts(self) -> float:
        return self._last_beat_ts

    @property
    def last_reflection_ts(self) -> float:
        return self._last_reflection_ts

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
            except Exception as e:
                warning(f"Reflection failed: {e}")
            finally:
                self._reflecting = False