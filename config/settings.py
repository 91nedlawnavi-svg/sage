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