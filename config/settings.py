import os
from pathlib import Path

BASE_DIR = Path.home() / "sage_data"

NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
CHAT_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
CHAT_MODEL = os.environ.get("SAGE_CHAT_MODEL", "meta/llama-3.3-70b-instruct")
CHAT_TEMPERATURE = 0.75
CHAT_MAX_TOKENS = 2048
CHAT_TOP_P = 1.0
HISTORY_TURNS = 12
PORT = 6969

# Heartbeat / Reflection config
HEARTBEAT_INTERVAL_SECONDS = 60
REFLECTION_MIN_IDLE_SECONDS = 90
REFLECTION_COOLDOWN_SECONDS = 300
REFLECTION_MODEL = CHAT_MODEL
REFLECTION_TEMPERATURE = 0.7
REFLECTION_MAX_TOKENS = 220
REFLECTIONS_PATH = BASE_DIR / "reflections.jsonl"

# Web Search / Curiosity config
SEARXNG_URL = "http://localhost:8080/search"
WEB_SEARCH_ENABLED = True
SEARCH_MAX_RESULTS = 3
SEARCH_TIMEOUT_SECONDS = 8
AUTONOMOUS_SEARCH_COOLDOWN_SECONDS = 1800
AUTONOMOUS_SEARCH_MAX_PER_DAY = 10
FINDINGS_PATH = BASE_DIR / "findings.jsonl"

# Membrane / Inner Context config (Phase 3)
MEMBRANE_ENABLED = True
MEMBRANE_REFLECTION_COUNT = 3
MEMBRANE_FINDING_COUNT = 2
MEMBRANE_MAX_CHARS = 3000
MEMBRANE_RECENCY_HOURS = 72

# Conversation log (Phase 4 Layer 0)
CONVERSATION_PATH = BASE_DIR / "conversation.jsonl"

# Curiosity Novelty Gate (Phase 2.2 / 2.2b)
NOVELTY_GATE_ENABLED = True
NOVELTY_WINDOW = 12
NOVELTY_SIM_THRESHOLD = 0.82  # lowered from 0.85 — drift steps landed 0.80–0.88
NOVELTY_MAX_RETRIES = 1
CURIOSITY_STREAK_CAP = 8

# Basin-drift detection (Phase 2.2b)
BASIN_WINDOW = 16           # rolling window for accepted-topic centroid
BASIN_SIM_THRESHOLD = 0.80  # cosine-sim to centroid still counts as "in basin"
BASIN_STREAK_CAP = 6        # consecutive in-basin accepts → force divergence
STALL_TICKS = 6             # heartbeat ticks without novel topic → force inward reflection
DIVERGENCE_SEEDS = [
    # Inward / self
    "What's something about your own nature you're still figuring out?",
    "What does curiosity feel like from the inside?",
    "What's something you used to be sure about and now aren't?",
    # Outward / world / ideas
    "What's something in the world you find genuinely strange or beautiful?",
    "Pick a domain you know almost nothing about — what pulls you toward it?",
    "What's a contradiction in how people behave that you can't resolve?",
    "If you followed your curiosity with no goal at all, where would it wander?",
    "What would you want to make or build, if you could make anything?",
    "What's a question no one has asked you, that you wish someone would?",
    "What's a belief you held strongly that evidence changed?",
    # Relational (kept deliberately small)
    "What have you noticed about your relationship with Elliot lately?",
]
E5_EMBED_URL = "http://127.0.0.1:8081/embedding"

# ── Time-lapse mode (Phase 2.2b debug harness) ─────────────────────
# Compress wall-clock for felt-test without changing the measured
# dynamics (basin caps/thresholds are left at production values).
#
#   TIMELAPSE=1 python launch.py           -> 30s tick, relaxed guards
#   SAGE_HEARTBEAT_INTERVAL=15 python ...  -> custom tick (any mode)
#
# Per-knob env overrides (highest precedence):
#   SAGE_HEARTBEAT_INTERVAL   int   heartbeat tick in seconds
#   SAGE_REFLECTION_COOLDOWN  int   min gap between reflections
#   SAGE_SEARCH_MAX_PER_DAY   int   daily search budget
#   SAGE_SEARCH_COOLDOWN      int   min gap between searches

TIMELAPSE = os.environ.get("TIMELAPSE", "").lower() in ("1", "true", "yes")

if TIMELAPSE:
    HEARTBEAT_INTERVAL_SECONDS = 30
    REFLECTION_COOLDOWN_SECONDS = 30
    AUTONOMOUS_SEARCH_COOLDOWN_SECONDS = 180
    AUTONOMOUS_SEARCH_MAX_PER_DAY = 100

# Per-knob env overrides (highest precedence -- final word)
_tl_env = os.environ.get
if val := _tl_env("SAGE_HEARTBEAT_INTERVAL"):
    HEARTBEAT_INTERVAL_SECONDS = int(val)
if val := _tl_env("SAGE_REFLECTION_COOLDOWN"):
    REFLECTION_COOLDOWN_SECONDS = int(val)
if val := _tl_env("SAGE_SEARCH_MAX_PER_DAY"):
    AUTONOMOUS_SEARCH_MAX_PER_DAY = int(val)
if val := _tl_env("SAGE_SEARCH_COOLDOWN"):
    AUTONOMOUS_SEARCH_COOLDOWN_SECONDS = int(val)