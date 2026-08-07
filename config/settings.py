import os
from pathlib import Path

BASE_DIR = Path(os.environ.get("SAGE_DATA_DIR", Path.home() / "sage_data")).expanduser()

# Omniroute (local router on :20128) is the sole chat endpoint for Sage.
# Earlier code called this NVIDIA_API_KEY because the router replaced raw
# NVIDIA calls without renaming the var; the var name is now honest.
OMNIROUTE_API_KEY = os.environ.get("OMNIROUTE_API_KEY", "")
CHAT_API_URL = "http://localhost:20128/v1/chat/completions"
CHAT_MODEL = os.environ.get("SAGE_CHAT_MODEL", "sage")
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
# Reasoning models burn hundreds of tokens thinking before the reflection —
# 220 truncated mid-thought (the router then returns raw reasoning as
# content). Ceiling covers reasoning burn + a full reflection; at 1024 about
# 1-in-8 still ended mid-sentence.
REFLECTION_MAX_TOKENS = 1536
REFLECTIONS_PATH = BASE_DIR / "reflections.jsonl"

# Voice (STT + TTS via Deepgram)
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
DEEPGRAM_TTS_MODEL = os.environ.get("SAGE_TTS_MODEL", "aura-2-luna-en")
DEEPGRAM_STT_MODEL = os.environ.get("SAGE_STT_MODEL", "nova-3")
DEEPGRAM_TTS_URL = "https://api.deepgram.com/v1/speak"
DEEPGRAM_STT_URL = "https://api.deepgram.com/v1/listen"
VOICE_TIMEOUT_SECONDS = 15
VOICE_ENABLED = bool(DEEPGRAM_API_KEY)

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

# Wave 2 memory core (SQLite). Off = legacy JSONL behavior, byte-identical.
# Flipped on at cutover (after migration verify OK), never before.
MEMORY_CORE_SQLITE = os.environ.get("SAGE_MEMORY_CORE", "0").lower() in ("1", "true", "yes")
# Benchmark harness can disable autonomous writers while retaining the full chat path.
SAGE_BACKGROUND_ENABLED = os.environ.get("SAGE_BACKGROUND_ENABLED", "1").lower() in ("1", "true", "yes")
# Scribe model for claim extraction — won the 12/12 bake-off (eval_harness,
# commit 7a1dfdc). Routed via the 'sage-scribe' Omniroute combo: CF
# Llama-3.3-70b ONLY, spread across 5 accounts for rate-limit headroom —
# single-model, so account failover can't change extraction behavior.
# KNOWN JITTER (measured 2026-07-19): this model at temp 0.1 is not
# deterministic on the eval battery — runs score 10-12/12, occasionally
# emitting a forbidden claim (fx07/fx08) or malformed JSON. The promotion
# desk gates all claims, so worst case is a junk nomination Elliot rejects.
# An earlier multi-model combo (3x70B) was rejected partly on this jitter
# being misread as fallback divergence; single-model is still the right call
# (no cross-model variation stacked on top of the base jitter).
EXTRACTION_SCRIBE_MODEL = os.environ.get(
    "SAGE_EXTRACTION_SCRIBE", "sage-scribe")

# Knowledge store (Phase 4 Layer 2 — derived notebooks)
KNOWLEDGE_ENABLED = os.environ.get("SAGE_KNOWLEDGE_ENABLED", "0").lower() in ("1", "true", "yes")
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
RELATIONAL_ENTITIES_PATH = KNOWLEDGE_DIR / "relational_entities.jsonl"
RELATIONAL_RELATIONS_PATH = KNOWLEDGE_DIR / "relational_relations.jsonl"
INTERIOR_ENTITIES_PATH = KNOWLEDGE_DIR / "interior_entities.jsonl"
INTERIOR_RELATIONS_PATH = KNOWLEDGE_DIR / "interior_relations.jsonl"

# Knowledge recall / fact embedding (Phase 4 Layer 2, semantic selection)
# Recalibrated 19 Jul 2026 for Qwen3-0.6B (bench/threshold_calibration.py);
# aligned with hybrid_retrieval.RERANK_SIM_FLOOR. Legacy (flag-off) path only.
KNOWLEDGE_FACT_MIN_SIM = float(os.environ.get("SAGE_KNOWLEDGE_FACT_MIN_SIM", "0.35"))
FACT_INDEX_PATH = KNOWLEDGE_DIR / "fact_embeddings.jsonl"
FACT_INDEX_BATCH = int(os.environ.get("SAGE_FACT_INDEX_BATCH", "16"))

# Semantic recall (Phase 4 Layer 1) — relevance-based memory of older
# conversation turns + reflections that have scrolled out of the context window.
RECALL_ENABLED = os.environ.get("SAGE_RECALL_ENABLED", "1").lower() in ("1", "true", "yes")
RECALL_INDEX_PATH = BASE_DIR / "recall_index.jsonl"
RECALL_TOP_K = int(os.environ.get("SAGE_RECALL_TOP_K", "4"))          # max items injected per turn
RECALL_MIN_SIM = float(os.environ.get("SAGE_RECALL_MIN_SIM", "0.33"))  # cosine floor to count as relevant (Qwen3 scale: probe noise ceiling ~0.33, bench/threshold_calibration.py 19 Jul 2026)
RECALL_MAX_CHARS = 2200            # char budget for the injected recall block
RECALL_MIN_CHARS = 24              # skip trivially short texts (indexing and querying)
RECALL_RECENT_EXCLUDE_TURNS = 24   # skip the N most recent conversation messages (already in the live window)
RECALL_RECENT_HOURS = MEMBRANE_RECENCY_HOURS  # reflections newer than this are already shown by the Membrane
RECALL_INDEX_BATCH = int(os.environ.get("SAGE_RECALL_INDEX_BATCH", "12"))  # embeds per indexing pass; e5 is GPU-fast now (~2s/embed) so a larger batch drains the backlog in minutes
RECALL_EMBED_SLEEP = 0.15          # seconds between sequential embeds while indexing
RECALL_REFLECTION_BACKFILL = int(os.environ.get("SAGE_RECALL_REFL_BACKFILL", "500"))  # only index the most recent N reflections
RECALL_EMBED_MAX_CHARS = int(os.environ.get("SAGE_RECALL_EMBED_MAX_CHARS", "1000"))  # cap text sent to the embedder per embed, matching the stored index text length
RECALL_INDEX_MAX_FAILS = int(os.environ.get("SAGE_RECALL_INDEX_MAX_FAILS", "3"))  # consecutive embed failures before a reindex pass bails (e5 likely down)
RECALL_INDEX_READ_TIMEOUT = float(os.environ.get("SAGE_RECALL_INDEX_READ_TIMEOUT", "12"))  # background indexer read ceiling; generous margin over the ~2s GPU embed

# Curiosity Novelty Gate (Phase 2.2 / 2.2b)
NOVELTY_GATE_ENABLED = True
NOVELTY_WINDOW = 12
NOVELTY_SIM_THRESHOLD = 0.35  # Qwen3 scale, percentile-mapped from e5 0.82 (bench/threshold_calibration.py 19 Jul 2026)
NOVELTY_MAX_RETRIES = 1
CURIOSITY_STREAK_CAP = 8

# Basin-drift detection (Phase 2.2b)
BASIN_WINDOW = 16           # rolling window for accepted-topic centroid
BASIN_SIM_THRESHOLD = 0.30  # cosine-sim to centroid still counts as "in basin" (Qwen3 scale, mapped from e5 0.80)
BASIN_STREAK_CAP = 6        # consecutive in-basin accepts → force divergence

# Reflection-stream basin detection (fix #5 / Phase 2.2c)
REFLECTION_BASIN_SIM_THRESHOLD = float(os.environ.get("SAGE_REFLECTION_BASIN_SIM", "0.49"))  # Qwen3 scale, mapped from e5 0.88
REFLECTION_BASIN_STREAK_CAP = int(os.environ.get("SAGE_REFLECTION_BASIN_CAP", "4"))
REFLECTION_DIVERGE_HOLD = int(os.environ.get("SAGE_REFLECTION_DIVERGE_HOLD", "2"))
REFLECTION_BASIN_WINDOW = 16
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
E5_EMBED_URL = "http://127.0.0.1:8081/embedding"  # embedder endpoint (Qwen3-0.6B since 19 Jul 2026; name kept for import stability)

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