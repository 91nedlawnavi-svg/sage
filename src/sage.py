"""Foreground Sage Foundation chat."""

from __future__ import annotations

import argparse
from pathlib import Path

from events import EventStore
from router import RouterClient

ROUTER_FAILURE = "Sage could not reach the local router. Your message was saved; no assistant reply was recorded."


def handle_message(message: str, store: EventStore, router: RouterClient) -> str:
    try:
        store.append("user", message)
    except OSError:
        return "Sage could not save your message. Nothing was sent."

    result = router.chat(message)
    if not result.succeeded:
        return ROUTER_FAILURE

    try:
        store.append("assistant", result.reply)
    except OSError:
        return "Sage received a reply but could not save it."
    return result.reply


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sage Foundation chat.")
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
