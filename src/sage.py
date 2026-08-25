"""Foreground Sage chat."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from events import Event, EventStore
from held_close import PrivacyDecision, classify
from router import RouterClient

HELD_CLOSE_ACKNOWLEDGEMENT = "I'm holding this close."
ROUTER_FAILURE = "Sage could not reach the local router. Your message was saved; no assistant reply was recorded."
SAVE_FAILURE = "Sage could not save your message. Nothing was sent."


@dataclass(frozen=True)
class AcceptedMessage:
    event: Event
    privacy: PrivacyDecision


def accept_message(message: str, store: EventStore, held_close: bool = False) -> AcceptedMessage | None:
    """Persist and classify user input before any provider can receive it."""
    try:
        privacy = classify(message, store.carry_before_next_user_event())
        if held_close:
            privacy = PrivacyDecision(True, privacy.tier, 0)
        event = store.append(
            "user",
            message,
            save_embedding=not privacy.held_close,
            initial_held_close=privacy.held_close,
            privacy_carry_after=privacy.carry_after,
        )
        try:
            store.append_privacy(
                event["id"],
                privacy.held_close,
                "user" if held_close else "sensor",
                carry_after=None if held_close else privacy.carry_after,
            )
        except OSError:
            pass
    except OSError:
        return None
    return AcceptedMessage(event, privacy)


def build_router_messages(
    message: str,
    store: EventStore,
    *,
    max_context: int = 8,
    exclude_event_id: str | None = None,
) -> list[dict[str, str]]:
    messages = [
        {"role": event["role"], "content": event["content"]}
        for event in store.recall(message, limit=max_context, exclude_event_id=exclude_event_id)
    ]
    messages.append({"role": "user", "content": message})
    return messages


def handle_message(message: str, store: EventStore, router: RouterClient) -> str:
    accepted = accept_message(message, store)
    if accepted is None:
        return SAVE_FAILURE
    if accepted.privacy.held_close:
        return HELD_CLOSE_ACKNOWLEDGEMENT

    result = router.chat_with_messages(build_router_messages(message, store, exclude_event_id=accepted.event["id"]))
    if not result.succeeded:
        return ROUTER_FAILURE

    try:
        store.append("assistant", result.reply)
    except OSError:
        return "Sage received a reply but could not save it."
    return result.reply


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sage chat.")
    parser.add_argument("--alias", required=True, help="Configured free-tier router alias")
    parser.add_argument("--data-root", type=Path, help="Event directory; defaults to ~/sage_data")
    args = parser.parse_args()

    store = EventStore(args.data_root)
    router = RouterClient(args.alias)
    while True:
        try:
            message = input("You: ").strip()
        except EOFError:
            print()
            return
        if message in {"/exit", "/quit"}:
            return
        if message:
            print(f"Sage: {handle_message(message, store, router)}")


if __name__ == "__main__":
    main()
