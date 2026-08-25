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


def load_dotenv() -> None:
    """Load KEY=VALUE pairs from .env without overriding the real environment."""
    env_file = REPO_ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

from events import EventStore
from heartbeat import Heartbeat
from interior import InteriorStore
from router import EmbeddingClient, RouterClient
from web import SageServer


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Launch Sage service.")
    parser.add_argument("--alias", default=os.getenv("SAGE_CHAT_MODEL", "or/opencode-zen/mimo-v2.5-free"), help="Chat model alias")
    parser.add_argument("--scribe-alias", default=os.getenv("SAGE_SCRIBE_MODEL", "sage-scribe"), help="Scribe model alias")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "6969")), help="Local web port")
    parser.add_argument("--data-root", type=Path, default=Path(os.getenv("SAGE_DATA_ROOT", str(Path.home() / "sage_data"))), help="Lived data root")
    args = parser.parse_args()

    embedder = EmbeddingClient()
    store = EventStore(args.data_root, embedder=embedder)
    router = RouterClient(args.alias)
    scribe_router = RouterClient(args.scribe_alias)
    interior = InteriorStore(args.data_root)

    # Start permitted local background work.
    heartbeat = Heartbeat(store, interior, scribe_router, interval_seconds=120.0)
    heartbeat.start()

    server = SageServer(("127.0.0.1", args.port), store, router, interior)
    print(f"Sage online at http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        heartbeat.stop()
        server.server_close()


if __name__ == "__main__":
    main()
