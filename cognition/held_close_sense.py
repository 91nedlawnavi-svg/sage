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
_T1 = re.compile(
    r"\b(confession|confess|secret|shame|embarrass|"
    r"suicid|self.harm|abuse|molest|assault|rape|"
    r"grief|bereave|funeral|died|dying|dead|overdos|"
    r"addict|relaps|in crisis|broke down|fell apart|"
    r"I can't cope|I can't do this|I don't know how to|"
    r"no one knows|never told anyone|I've never said)\b",
    re.IGNORECASE,
)
_T2 = re.compile(
    r"\b(cheated|cheating|affair|divorce|separation|"
    r"mental health|depression|depressed|anxiety|"
    r"trauma|PTSD|therapy|therapist|"
    r"fired|laid off|lost my job|bankrupt|"
    r"stole from|stealing from|"
    r"gay|lesbian|queer|bi |trans |transition|coming out|"
    r"addiction|alcoholic|drinking problem|"
    r"my brother|my sister|my mother|my father|my parent|"
    r"estranged|cut off|no contact)\b",
    re.IGNORECASE,
)
_T3 = re.compile(
    r"\b(scared|terrified|terrifying|overwhelmed|"
    r"fight with|argument with|falling apart|"
    r"not sure I|struggling to|hard to admit|"
    r"cancer|diagnosis|chronic|disability)\b",
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
            # Moderate: flag if already in a span, else start cautiously
            if _span_counter > 0:
                _span_counter = SPAN_COOLDOWN  # refresh carry
                return True
            # Single moderate signal without prior span: flag it but with shorter carry
            _span_counter = 2
            return True
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
