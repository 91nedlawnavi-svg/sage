"""Inner context selector for the Membrane (Phase 3).

Pulls Sage's own persisted reflections and findings and formats them
into a clearly-delimited block for injection into the chat prompt.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from config.settings import (
    MEMBRANE_ENABLED,
    MEMBRANE_REFLECTION_COUNT,
    MEMBRANE_FINDING_COUNT,
    MEMBRANE_MAX_CHARS,
    MEMBRANE_RECENCY_HOURS,
    REFLECTIONS_PATH,
    FINDINGS_PATH,
)


def _read_jsonl_safe(path: Path) -> list[dict]:
    """Read JSONL file, return empty list on any failure (missing, corrupt, etc.)."""
    if not path.exists():
        return []
    entries = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        return []
    return entries


def _filter_recent(entries: list[dict], hours: int) -> list[dict]:
    """Filter entries to only those within the recency window.

    Handles naive ISO timestamps (treated as UTC), UTC-with-Z, and
    offset-aware timestamps (converted to UTC before comparison).
    Never raises — malformed timestamps are silently skipped.
    """
    if not entries:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = []
    for e in entries:
        ts_str = e.get("ts", "")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            if ts >= cutoff:
                recent.append(e)
        except Exception:
            continue
    return recent


def select_inner_context() -> str | None:
    """Select and format Sage's inner context for the chat prompt.

    Returns the formatted block or None if disabled/empty/error.
    Never raises — degrades silently to no inner context.
    """
    if not MEMBRANE_ENABLED:
        return None

    # Load raw data from the SAME stores the daemon writes / API reads
    reflections = _read_jsonl_safe(REFLECTIONS_PATH)
    findings = _read_jsonl_safe(FINDINGS_PATH)

    # Filter to recency window
    reflections = _filter_recent(reflections, MEMBRANE_RECENCY_HOURS)
    findings = _filter_recent(findings, MEMBRANE_RECENCY_HOURS)

    if not reflections and not findings:
        return None

    # Take most recent N
    reflections = reflections[-MEMBRANE_REFLECTION_COUNT:]
    findings = findings[-MEMBRANE_FINDING_COUNT:]

    # Build the block
    lines = [
        "[SAGE'S INNER CONTEXT]",
        "The following is YOUR OWN recent inner life — reflections you had and things you "
        "got curious about on your own. It is yours, not the user's. Let it inform how you "
        "respond, as your perspective. You are Sage; the user is a separate person.",
        "",
    ]

    if reflections:
        lines.append("Recent reflections:")
        for r in reflections:
            text = r.get("text", "").strip()
            if text:
                # Truncate to keep block bounded
                lines.append(f"  - {text[:500]}")
        lines.append("")

    if findings:
        lines.append("Recently curious about:")
        for f in findings:
            query = f.get("query", "").strip()
            results = f.get("results", [])
            if not query:
                continue
            # Summarize findings with provenance
            source_lines = []
            for r in results[:2]:
                title = r.get("title", "").strip()
                url = r.get("url", "").strip()
                if title and url:
                    source_lines.append(f"{title} ({url})")
            if source_lines:
                lines.append(f"  - {query}  [sources: {', '.join(source_lines)}]")
            else:
                lines.append(f"  - {query}  [no results]")
        lines.append("")

    block = "\n".join(lines).strip()

    # Enforce max char budget
    if len(block) > MEMBRANE_MAX_CHARS:
        block = block[:MEMBRANE_MAX_CHARS] + "\n...[truncated]"

    return block if block else None