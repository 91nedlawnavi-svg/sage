from pathlib import Path

DIRECTIVE_PATH = Path(__file__).parent.parent / "directive.txt"


def load_directive() -> str:
    """Read the directive file fresh on EVERY call (hot-reload)."""
    if not DIRECTIVE_PATH.exists():
        raise RuntimeError(f"Directive file not found at {DIRECTIVE_PATH}")
    content = DIRECTIVE_PATH.read_text().strip()
    if not content:
        raise RuntimeError("Directive file is empty")
    return content


def get_directive() -> str:
    """Get the current directive. No reflection/automated path may write it."""
    return load_directive()