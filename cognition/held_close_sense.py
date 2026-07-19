"""Held-close span sensor — Blueprint §2.8.

LLM-free heuristic: detects when a conversation turn carries confessional
weight and should be flagged held-close. No LLM call — must be instantaneous
on the chat path.

Design: keyword/pattern tiers with a lightweight span-carry check.
- Tier 1 (very strong): explicit confession markers, grief, abuse, crisis
- Tier 2 (strong): family fracture, mental health, betrayal, sexuality
- Tier 3 (moderate): health anxiety, relationship tension, financial stress

Span carry: once weight enters, subsequent turns inherit held-close until the
air genuinely clears (topic change detected by absence of weight signals for
SPAN_COOLDOWN turns). This is Elliot's judgment call to override via tap.

Blueprint §2.8: "once weight enters, subsequent turns inherit held-close until
the air genuinely clears (topic change, tone lift; her judgment, correctable)"
"""
from __future__ import annotations

import re

# ── weight patterns (compiled once) ──────────────────────────────────────────
# Tuned tight on purpose: a false flag silently pulls the episode out of
# extraction/consolidation, so bare kinship words ("my sister"), idioms
# ("phone died"), and everyday stress ("argument with the vendor") must NOT
# match. Elliot's tap-toggle covers what the sensor misses.
_T1 = re.compile(
    r"\b(confession|confess|deepest secret|ashamed|"
    r"suicid|self.harm|molest|assault|rape|"
    r"passed away|funeral|grief|bereave|overdos|"
    r"in crisis|I broke down|I fell apart|"
    r"I can't cope|I can't do this anymore|"
    r"no one knows|never told anyone|I've never said)",
    re.IGNORECASE,
)
_T2 = re.compile(
    r"\b(cheated on|cheating on|affair|divorce|"
    r"(?<!great )depression|depressed|my anxiety|"
    r"hate myself|"
    r"trauma|PTSD|my therapist|in therapy|"
    r"abused|abusive|"
    r"stole from me|stealing from me|"
    r"coming out|closeted|"
    r"addiction|alcoholic|drinking problem|"
    r"estranged|went no contact)",
    re.IGNORECASE,
)
_T3 = re.compile(
    r"\b(terrified|overwhelmed|"
    r"hard to admit|hard to say this|"
    r"cancer|my diagnosis|chronic pain|"
    r"worthless|hopeless)",
    re.IGNORECASE,
)

# Cooldown: how many low-weight turns clears a span in-memory
SPAN_COOLDOWN = 4

# Per-session span tracker: {session_id: cooldown_remaining}
# Using a simple module-level dict — single-user, single-process, no persistence needed.
_span_counter: int = 0  # turns-of-weight-carry remaining


def _weight(text: str) -> int:
    """Return weight tier (0=none, 1=moderate, 2=strong, 3=very strong)."""
    if _T1.search(text):
        return 3
    if _T2.search(text):
        return 2
    if _T3.search(text):
        return 1
    return 0


def sense(content: str) -> bool:
    """True if this turn should be flagged held-close.

    Called at intake for every Elliot turn. Updates the in-memory span counter.
    Never raises.
    """
    global _span_counter
    try:
        w = _weight(content)
        if w >= 2:
            # Strong/very strong: flag this turn, reset span carry
            _span_counter = SPAN_COOLDOWN
            return True
        if w == 1:
            # Moderate: only meaningful inside an existing span. A lone T3
            # signal ("overwhelmed", "cancer" in a news remark) is not a
            # confession — flagging it would silently starve extraction.
            if _span_counter > 0:
                _span_counter = SPAN_COOLDOWN  # refresh carry
                return True
            return False
        # No signal: drain cooldown
        if _span_counter > 0:
            _span_counter -= 1
            # Carry: flag even without new signal
            return True
        return False
    except Exception:
        return False


def conversation_has_weight(recent_turns: list[str]) -> bool:
    """True if recent conversation turns contain weight signals.

    Used by the tactful recall gate: should a held-close memory be surfaced?
    Checks the last N turns for weight signals without modifying span state.
    """
    try:
        for t in recent_turns:
            if _weight(t) >= 1:
                return True
        return False
    except Exception:
        return False
