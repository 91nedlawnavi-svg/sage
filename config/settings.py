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