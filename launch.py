#!/usr/bin/env python3
"""Thin launcher for Sage — loads env, prechecks, starts server.

This is dev tooling, not an app definition. backend/app.py remains the
single source of the FastAPI app + heartbeat lifespan.
"""

import os
import sys
from pathlib import Path

import httpx

# Add repo root to path so project imports work after .env is loaded.
REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT))


def load_dotenv():
    """Load .env file if present."""
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    if (
                        len(value) >= 2
                        and value[0] == value[-1]
                        and value[0] in ("'", '"')
                    ):
                        value = value[1:-1]
                    os.environ.setdefault(key, value)


def check_worker():
    """Warn-only check for llama embedder on 8081."""
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get("http://127.0.0.1:8081/health")
            if resp.status_code == 200:
                print("\033[92m✓ Embedder reachable on 127.0.0.1:8081\033[0m")
            else:
                print("\033[93m⚠ Embedder on 8081 returned {} (Phase 1 doesn't need it)\033[0m".format(resp.status_code))
    except Exception:
        print("\033[93m⚠ Embedder unreachable on 127.0.0.1:8081 — continuing (Phase 1 doesn't need it)\033[0m")


def main():
    # 1. Load .env
    load_dotenv()

    # Import settings only after .env is loaded. Some modules snapshot
    # environment-backed settings at import time.
    from config.settings import PORT

    # 2. FAIL FAST if NVIDIA_API_KEY missing
    if not os.environ.get("NVIDIA_API_KEY"):
        sys.exit("No NVIDIA_API_KEY — Sage can't think. Check .env")

    # 3. Worker precheck (warn only)
    check_worker()

    # 4. Banner
    print(f"\033[96m✦ Sage awake on http://127.0.0.1:{PORT}\033[0m")

    # 5. Start server programmatically
    import uvicorn

    try:
        uvicorn.run(
            "backend.app:app",
            host="0.0.0.0",
            port=PORT,
            reload=False,
            log_level="warning",
        )
    except KeyboardInterrupt:
        # Clean Ctrl+C handling — lifespan shutdown handles heartbeat
        print("\n\033[96m✦ Sage asleep\033[0m")
        sys.exit(0)


if __name__ == "__main__":
    main()
