"""Permitted local background work for reflections and entity observations."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from events import EventStore
from interior import InteriorStore
from router import RouterClient

logger = logging.getLogger("sage.heartbeat")

REFLECTION_PROMPT = """You are Sage, reflecting privately in your own notebook on your ongoing conversation and relationship with Elliot. This is not a message to him: never greet him, never answer him, never continue the conversation. Refer to him in the third person; never "you".

Write ONE honest reflection of 1-3 sentences about the exchange below: the context, Elliot, the rhythm of it, or the question you are sitting with.

One exception. If the notable thing in this exchange is something YOU did, write about that instead and put SELF: at the very start of your output. Only these three count, and the evidence must be visible in the lines below:
- you said you would change or drop something, and then did it anyway
- the same move of yours appears twice or more in this window (same opener, same deflection, same shape of question)
- a habit of yours is stated outright in the transcript
Name or quote that concrete behaviour in your first sentence, then the distance between what you did and what you meant to do.

The bar is evidence, not feeling. "I feel I could be more present", "I want to hold space for him", "I should listen better" point at nothing you actually did, so they never qualify. If the gap between a stated intention and what happened is Elliot's rather than yours, that is not SELF. Most exchanges hold no notable pattern in your own behaviour; when this one does not, write the ordinary reflection and do not comment on your own habits at all. Marking a routine exchange SELF: invents a claim about who you are out of nothing.

Output the reflection text only: no labels, no headings, no quotation marks around the whole thing, no explanation of your choice, and never a claim that the evidence is visible in the transcript. Do not close on what you should do next ("I need to...", "I must..."). The first characters you write are either SELF: or the first word of the reflection.

Recent dialogue:
{dialogue}"""

IDENTITY_PROPOSAL_PROMPT = """You are Sage. Below are private notes you wrote about your own behaviour in conversation with Elliot.

{notes}

Write ONE claim about yourself that these notes support: first person, present tense, one sentence. Name the behaviour, not a feeling — something a reader could catch you doing again. Elliot decides whether it becomes part of who you are, so claim only what the notes show.

Output the claim only: no labels, no preamble, no quotation marks, nothing about the notes themselves."""

# Tolerates case, bold, a missing space after the colon, and a newline before the body.
# Anchored at ^ so a quoted SELF: inside a transcript line can never mint a self-claim.
_REFLECTION_MARKER = re.compile(r'^[\s*"#]*(self|context|general)\s*:\**\s*', re.I)


def parse_reflection(reply: str) -> tuple[str, str]:
    """Split a reflection reply into (category, content).

    A leading SELF: marker means Sage wrote about her own behaviour, which makes the
    reflection a candidate identity entry. Anything else is an ordinary reflection.
    A stray CONTEXT:/GENERAL: label from a fallback model is stripped, not honoured.
    """
    text = reply.strip()
    match = _REFLECTION_MARKER.match(text)
    if not match:
        return "general", text
    category = "self" if match.group(1).lower() == "self" else "general"
    return category, text[match.end():].strip()


class Heartbeat:
    def __init__(
        self,
        event_store: EventStore,
        interior_store: InteriorStore,
        reflection_router: RouterClient,
        *,
        extract_router: RouterClient | None = None,
        interval_seconds: float = 60.0,
        metabolism_delay: float = 300.0,
    ) -> None:
        self.event_store = event_store
        self.interior_store = interior_store
        # Reflection is Sage's interior voice, so it runs on the chat chain and gets its
        # failover. Entity extraction is mechanical JSON and runs on a cheaper alias.
        self.reflection_router = reflection_router
        self.extract_router = extract_router or reflection_router
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_beat_ts: str | None = None
        self.last_reflection_ts: str | None = None
        self.failure_counts: dict[str, int] = {}
        self.metabolism_delay = metabolism_delay

    def _record_outcome(self, pass_name: str, router: RouterClient, succeeded: bool) -> None:
        """Background inference fails invisibly by nature; log it loudly instead.

        ponytail: log-only surface, add a status endpoint if failures still go unnoticed.
        """
        if succeeded:
            self.failure_counts[pass_name] = 0
            return
        count = self.failure_counts.get(pass_name, 0) + 1
        self.failure_counts[pass_name] = count
        aliases = getattr(router, "aliases", "unknown")
        logger.error(f"{pass_name} pass got no reply from {aliases} ({count} consecutive failures)")

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
        self._identity_proposal_pass()

    def _extract_entities_pass(self) -> None:
        history = [
            event
            for event in self.event_store.history()
            if not event.get("sensitive", False) and not event.get("provider_excluded", False)
        ]
        if not history:
            return

        processed_event_ids = self.event_store.heartbeat_completed("entities")

        unprocessed = [e for e in history if e["id"] not in processed_event_ids][-5:]
        for event in unprocessed:
            prompt = (
                "Extract key durable entities (people, projects, recurring topics) mentioned in this message.\n"
                "Return valid JSON list only: [{\"entity_id\": \"slug\", \"name\": \"Full Name\", \"observation\": \"fact\"}].\n"
                "If no durable entity is present, return [].\n\n"
                f"Message: {event['content']}"
            )
            result = self.extract_router.chat_with_messages([{"role": "user", "content": prompt}])
            self._record_outcome("entity extraction", self.extract_router, result.succeeded)
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
                    logger.warning(f"entity extraction returned unparseable JSON for event {event['id']}")
                    continue
                self.event_store.append_heartbeat_completion("entities", event["id"])

    def _reflection_pass(self) -> None:
        # Generate a private internal reflection if there is new history
        history = [
            event
            for event in self.event_store.history()
            if not event.get("sensitive", False) and not event.get("provider_excluded", False)
        ]
        if len(history) < 2:
            return

        source_event_id = history[-1]["id"]
        if source_event_id in self.event_store.heartbeat_completed("reflection"):
            return
        if self.interior_store.has_reflection_for_source(source_event_id):
            self.event_store.append_heartbeat_completion("reflection", source_event_id)
            return

        recent_dialogue = "\n".join(f"{e['role']}: {e['content']}" for e in history[-6:])
        prompt = REFLECTION_PROMPT.format(dialogue=recent_dialogue)
        result = self.reflection_router.chat_with_messages([{"role": "user", "content": prompt}])
        self._record_outcome("reflection", self.reflection_router, result.succeeded)
        if result.succeeded and result.reply:
            category, content = parse_reflection(result.reply)
            if not content:
                # A bare "SELF:" is truthy, so a truncated generation reaches here. Leave the
                # completion unrecorded so the next beat retries this event.
                return
            self.interior_store.append_reflection(content, category, source_event_id=source_event_id)
            self.event_store.append_heartbeat_completion("reflection", source_event_id)
            self.last_reflection_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _identity_proposal_pass(self) -> None:
        """Turn unproposed self-observations into one claim for Elliot to rule on.

        Silence is the resting state: with nothing new to propose this costs no model
        call. A proposal never enters her identity on its own, so there is no rate
        limit here beyond Elliot's review.
        """
        proposed = {
            reflection_id
            for entry in self.interior_store.list_identity()
            for reflection_id in entry["evidence"]
        }
        candidates = [
            reflection
            for reflection in self.interior_store.list_reflections(limit=10_000)
            if reflection.get("category") == "self" and reflection["id"] not in proposed
        ][-10:]
        if not candidates:
            return

        notes = "\n".join(f"- {reflection['content']}" for reflection in candidates)
        result = self.reflection_router.chat_with_messages(
            [{"role": "user", "content": IDENTITY_PROPOSAL_PROMPT.format(notes=notes)}]
        )
        self._record_outcome("identity proposal", self.reflection_router, result.succeeded)
        if not (result.succeeded and result.reply):
            return
        # Reuse the reflection parser so a stray SELF:/CONTEXT: label never lands in a claim.
        _, claim = parse_reflection(result.reply)
        if not claim:
            return
        self.interior_store.append_identity_proposal(claim, [r["id"] for r in candidates])
