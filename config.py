"""
config.py — Sage system configuration
All tuneable constants live here. Edit freely.
"""
import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────
BASE_DIR        = Path.home() / "sage_data"
DIRECTIVE_FILE  = BASE_DIR / "directive.txt"
HISTORY_FILE    = BASE_DIR / "chat_history.txt"
DATA_DIR        = BASE_DIR / "data"
EPISODIC_DIR    = DATA_DIR / "episodic"
EMOTIONAL_DIR   = DATA_DIR / "emotional"
REFLECTIONS_DIR = DATA_DIR / "reflections"
EMBEDDINGS_DIR  = DATA_DIR / "embeddings"

# Legacy library — kept for manual editing via UI
LIBRARY_DIR  = BASE_DIR / "library"
LIBRARY_CATS = ["people", "places", "topics"]

# ── NVIDIA ───────────────────────────────────────────────────────────
NVIDIA_API_KEY   = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_API_URL   = "https://integrate.api.nvidia.com/v1/chat/completions"
REFLECTION_MODEL = os.environ.get("REFLECTION_MODEL", "mistralai/mistral-small-4-119b-2603")

# ── Inference endpoints ──────────────────────────────────────────────
CHAT_API_URL  = "https://integrate.api.nvidia.com/v1/chat/completions"
CHAT_MODEL    = "meta/llama-3.3-70b-instruct"

MEM_API_URL   = "http://localhost:8081/v1/chat/completions"
EMBED_API_URL = "http://localhost:8082/v1/embeddings"

# ── Inference parameters ─────────────────────────────────────────────
CHAT_TEMPERATURE = 0.75
CHAT_MAX_TOKENS  = 2048
CHAT_TOP_P       = 0.9

MEM_TEMPERATURE  = 0.1
MEM_MAX_TOKENS   = 512
MEM_TOP_P        = 0.9

# ── Retrieval ────────────────────────────────────────────────────────
TOP_K_MEMORIES      = 4     # max memory chunks injected per turn
EMBED_CACHE_MAX     = 512   # in-memory LRU cap
EMBED_PREFIX        = "Represent this sentence for searching relevant passages: "
RETRIEVAL_THRESHOLD = 0.35  # minimum cosine similarity to include a memory

# ── Conversation ─────────────────────────────────────────────────────
HISTORY_TURNS = 12  # recent turns kept in prompt window

# ── Daemon triggers ──────────────────────────────────────────────────
DAEMON_TURN_TRIGGER     = 6    # reflect after N assistant turns
DAEMON_EMOTION_TRIGGER  = 3    # reflect if emotional signal in last N turns
DAEMON_COOLDOWN_SECONDS = 300  # minimum seconds between daemon runs

# ── Server ───────────────────────────────────────────────────────────
PORT = 6969
