import sys
import json
from datetime import datetime


def log(level: str, message: str, **kwargs):
    """Tiny structured logger."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "level": level.upper(),
        "message": message,
        **kwargs,
    }
    print(json.dumps(entry), file=sys.stderr)


def info(message: str, **kwargs):
    log("info", message, **kwargs)


def warning(message: str, **kwargs):
    log("warning", message, **kwargs)


def error(message: str, **kwargs):
    log("error", message, **kwargs)