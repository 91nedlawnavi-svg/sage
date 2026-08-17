"""Background heartbeat process for reflections, entity extraction, and revisable reach."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from events import EventStore
from interior import InteriorStore
from router import RouterClient

logger = logging.getLogger("sage.heartbeat")


class Heartbeat:
    def __init__(
        self,
        event_store: EventStore,
        interior_store: InteriorStore,
        scribe_router: RouterClient,
        *,
        interval_seconds: float = 60.0,
    ) -> None:
        self.event_store = event_store
        self.interior_store = interior_store
        self.scribe_router = scribe_router
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_beat_ts: str | None = None
        self.last_reflection_ts: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.beat()
            except Exception as e:
                logger.warning(f"Heartbeat error: {e}")
            self._stop_event.wait(self.interval_seconds)

    def beat(self) -> None:
        self.last_beat_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._extract_entities_pass()
        self._reflection_pass()

    def _extract_entities_pass(self) -> None:
        # Collect recent unheld events for entity observation extraction
        history = [e for e in self.event_store.history() if not e.get("held_close", False)]
        if not history:
            return

        existing_obs = self.event_store.entity_observations()
        processed_event_ids = {obs.get("source_event_id") for obs in existing_obs if obs.get("source_event_id")}

        unprocessed = [e for e in history if e["id"] not in processed_event_ids][-5:]
        for event in unprocessed:
            prompt = (
                "Extract key durable entities (people, projects, recurring topics) mentioned in this message.\n"
                "Return valid JSON list only: [{\"entity_id\": \"slug\", \"name\": \"Full Name\", \"observation\": \"fact\"}].\n"
                "If no durable entity is present, return [].\n\n"
                f"Message: {event['content']}"
            )
            result = self.scribe_router.chat_with_messages([{"role": "user", "content": prompt}])
            if result.succeeded and result.reply:
                try:
                    cleaned = result.reply.strip()
                    if cleaned.startswith("```json"):
                        cleaned = cleaned[7:]
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3]
                    items = json.loads(cleaned.strip())
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict) and "entity_id" in item and "name" in item:
                                self.event_store.append_entity_observation(
                                    entity_id=str(item["entity_id"]),
                                    name=str(item["name"]),
                                    observation=str(item.get("observation", "")),
                                    source_event_id=event["id"],
                                )
                except (json.JSONDecodeError, ValueError):
                    pass

    def _reflection_pass(self) -> None:
        # Generate a private internal reflection if there is new history
        history = [e for e in self.event_store.history() if not e.get("held_close", False)]
        if len(history) < 2:
            return

        recent_dialogue = "\n".join(f"{e['role']}: {e['content']}" for e in history[-6:])
        prompt = (
            "You are Sage reflecting privately on your ongoing conversation and relationship with Elliot.\n"
            "Write a concise, honest 1-3 sentence internal reflection about current context, rhythm, or questions.\n"
            "Do not address the user. This is your private notebook.\n\n"
            f"Recent dialogue:\n{recent_dialogue}"
        )
        result = self.scribe_router.chat_with_messages([{"role": "user", "content": prompt}])
        if result.succeeded and result.reply:
            self.interior_store.append_reflection(result.reply.strip())
            self.last_reflection_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
