"""Unified entrypoint for Sage daemon and web server."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add src directory to path
REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))


def load_dotenv() -> dict[str, str]:
    """Load KEY=VALUE pairs from .env and return its exact values."""
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return {}
    values = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        values[key] = value
        os.environ.setdefault(key, value)
    return values


def chat_models(dotenv: dict[str, str]) -> tuple[str, ...]:
    aliases = tuple(
        item.strip()
        for item in dotenv.get("SAGE_CHAT_MODELS", "").split(",")
        if item.strip()
    )
    if not aliases:
        raise ValueError("SAGE_CHAT_MODELS must list at least one model in .env")
    return aliases

from database import relational_db, interior_db  # noqa: E402
from events import EventStore  # noqa: E402
from heartbeat import Heartbeat  # noqa: E402
from interior import InteriorStore  # noqa: E402
from router import EmbeddingClient, RouterClient  # noqa: E402
from web import SageServer  # noqa: E402


def main() -> None:
    dotenv = load_dotenv()
    parser = argparse.ArgumentParser(description="Launch Sage service.")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "6969")), help="Local web port")
    parser.add_argument("--data-root", type=Path, default=Path(os.getenv("SAGE_DATA_ROOT", str(Path.home() / "sage_data"))), help="Lived data root")
    args = parser.parse_args()

    try:
        aliases = chat_models(dotenv)
    except ValueError as exc:
        parser.error(str(exc))

    # SQLite mirrors — derived from JSONL, never primary
    rel_mirror = relational_db(args.data_root)
    int_mirror = interior_db(args.data_root)

    embedder = EmbeddingClient()
    store = EventStore(args.data_root, embedder=embedder, mirror=rel_mirror)
    router = RouterClient(aliases)
    metabolism_delay = float(os.getenv("SAGE_METABOLISM_DELAY", "300"))
    interior = InteriorStore(args.data_root, mirror=int_mirror)

    # Start permitted local background work.
    heartbeat = Heartbeat(store, interior, router, interval_seconds=120.0, metabolism_delay=metabolism_delay)
    heartbeat.start()

    server = SageServer(("0.0.0.0", args.port), store, router, interior, rel_mirror, int_mirror)
    print(f"Sage online on http://0.0.0.0:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        heartbeat.stop()
        server.server_close()
        rel_mirror.close()
        int_mirror.close()


if __name__ == "__main__":
    main()
