"""
daemon/reflection_daemon.py — Event-driven background reflection

This is Sage's "slow mind" — it processes recent conversations
asynchronously, without blocking chat.

Philosophy:
  - Triggered by events, not a continuous loop
  - Runs at most once per DAEMON_COOLDOWN_SECONDS
  - Does three things: extract episode → extract emotions → reflect
  - Lightweight: runs on the Qwen3B memory model (CPU only)

Triggers:
  - Called manually after N assistant turns
  - Called when emotional signal detected in conversation
"""

import asyncio
import time
from typing import Optional

import httpx

from config import DAEMON_COOLDOWN_SECONDS
from cognition.emotional_analysis import extract_and_persist_emotions
from cognition.library_extraction import extract_and_populate_library
from cognition.synthesis import extract_episode, generate_reflection
from utils.logger import log


class ReflectionDaemon:
    """
    Stateful daemon that tracks last run time and queues work.
    One instance lives for the lifetime of the server process.
    """

    def __init__(self, client: httpx.AsyncClient):
        self._client     = client
        self._last_run   = 0.0          # epoch seconds
        self._running    = False        # prevent overlapping runs
        self._task: Optional[asyncio.Task] = None

    def should_run(self) -> bool:
        """True if cooldown has elapsed and daemon is idle."""
        elapsed = time.time() - self._last_run
        return (not self._running) and (elapsed >= DAEMON_COOLDOWN_SECONDS)

    def trigger(self, conversation_digest: str) -> None:
        """
        Fire-and-forget: schedule a daemon run if conditions are met.
        Safe to call from request handlers.
        """
        if not self.should_run():
            return
        self._task = asyncio.create_task(
            self._run(conversation_digest),
            name="reflection_daemon",
        )

    async def _run(self, conversation_digest: str) -> None:
        """
        Full reflection cycle:
          1. Extract episodic memory from digest
          2. Extract emotional themes from digest
          3. Generate a reflection from accumulated memory

        Each step is independent — a failure in one doesn't abort the others.
        """
        self._running  = True
        self._last_run = time.time()

        log("daemon", "cycle_start")

        try:
            # Step 1 — Episodic extraction
            try:
                await extract_episode(conversation_digest, self._client)
            except Exception as e:
                log("daemon", "error", step="episodic", error=str(e))

            # Step 2 — Emotional theme extraction
            try:
                await extract_and_persist_emotions(conversation_digest, self._client)
            except Exception as e:
                log("daemon", "error", step="emotional", error=str(e))

            # Step 3 — Reflection synthesis (from accumulated memory, not just this digest)
            try:
                await generate_reflection(self._client)
            except Exception as e:
                log("daemon", "error", step="reflection", error=str(e))

            # Step 4 — Library auto-population
            try:
                await extract_and_populate_library(conversation_digest, self._client)
            except Exception as e:
                log("daemon", "error", step="library", error=str(e))

        finally:
            self._running = False
            log("daemon", "cycle_complete")

    async def force_run(self, conversation_digest: str) -> None:
        """
        Run the full cycle immediately, ignoring cooldown.
        Used for first-boot history distillation.
        """
        self._running = False  # override lock
        await self._run(conversation_digest)
