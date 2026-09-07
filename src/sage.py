"""Foreground Sage chat."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal

from events import Event, EventStore
from router import RouterClient

ROUTER_FAILURE = "Sage could not reach the local router. Your message was saved; no assistant reply was recorded."
SAVE_FAILURE = "Sage could not save your message. Nothing was sent."
DIRECTIVE_PATH = Path(__file__).resolve().parents[1] / "directive.txt"


def accept_message(
    message: str,
    store: EventStore,
    *,
    source: Literal["text", "voice"] = "text",
) -> Event | None:
    """Persist user input before any provider can receive it."""
    try:
        return store.append("user", message, source=source)
    except OSError:
        return None


def build_router_messages(
    message: str,
    store: EventStore,
    *,
    max_context: int = 8,
    exclude_event_id: str | None = None,
    directive: str | None = None,
    search_context: str = "",
) -> list[dict[str, str]]:
    full_history = [
        event
        for event in store.history()
        if event["role"] in {"user", "assistant"}
        and event["id"] != exclude_event_id
    ]
    visible_ids = {event["id"] for event in store.visible_history()}
    eligible_history = [event for event in full_history if event["id"] in visible_ids]
    recent = eligible_history[-min(4, max_context):] if max_context > 0 else []
    recall_query = "\n".join(f"{event['role']}: {event['content']}" for event in (*recent, {"role": "user", "content": message}))
    recent_ids = {event["id"] for event in recent}
    remaining = max_context - len(recent)
    recalled = store.recall(
        recall_query,
        limit=len(full_history),
        exclude_event_id=exclude_event_id,
        fallback=False,
    ) if remaining > 0 else []
    older_ids = [event["id"] for event in recalled if event["id"] not in recent_ids]
    selected_ids = recent_ids | set(older_ids[:remaining])
    context = [event for event in full_history if event["id"] in selected_ids]
    messages = [{"role": event["role"], "content": event["content"]} for event in context]
    if directive:
        messages.insert(0, {"role": "system", "content": directive})
    if search_context:
        messages.append({"role": "system", "content": search_context})
    messages.append({"role": "user", "content": message})
    return messages


def compose_identity_block(interior) -> str:
    """Build the ratified-identity suffix for the directive, or "" on any failure."""
    try:
        entries = interior.list_identity()
    except Exception:
        return ""
    ratified = [e for e in entries if e.get("status") == "ratified"]
    if not ratified:
        return ""
    # Newest first, capped at 10
    ratified.sort(key=lambda e: e.get("said_at", ""), reverse=True)
    ratified = ratified[:10]
    claims = "\n".join(f"- {e['claim']}" for e in ratified)
    return f"\n\n---\n\nThings I have noticed about myself, and Elliot has confirmed:\n\n{claims}"


def load_directive(path: Path = DIRECTIVE_PATH, *, identity_block: str = "") -> str:
    try:
        directive = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return directive + identity_block


def handle_message(message: str, store: EventStore, router: RouterClient) -> str:
    accepted = accept_message(message, store)
    if accepted is None:
        return SAVE_FAILURE

    result = router.chat_with_messages(
        build_router_messages(
            message,
            store,
            exclude_event_id=accepted["id"],
            directive=load_directive(),
        )
    )
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
