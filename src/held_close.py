"""Local held-close intake classifier."""

from __future__ import annotations

from dataclasses import dataclass
import re


STRONG = re.compile(
    r"\b(confession|confess|deepest secret|ashamed|"
    r"suicid|self.harm|molest|assault|rape|"
    r"passed away|funeral|grief|bereave|overdos|"
    r"in crisis|I broke down|I fell apart|"
    r"I can't cope|I can't do this anymore|"
    r"no one knows|never told anyone|I've never said|"
    r"cheated on|cheating on|affair|divorce|"
    r"(?<!great )depression|depressed|my anxiety|"
    r"hate myself|trauma|PTSD|my therapist|in therapy|"
    r"abused|abusive|stole from me|stealing from me|"
    r"coming out|closeted|addiction|alcoholic|drinking problem|"
    r"estranged|went no contact)",
    re.IGNORECASE,
)
MODERATE = re.compile(
    r"\b(terrified|overwhelmed|hard to admit|hard to say this|"
    r"cancer|my diagnosis|chronic pain|worthless|hopeless)",
    re.IGNORECASE,
)
CARRY_TURNS = 4


@dataclass(frozen=True)
class PrivacyDecision:
    held_close: bool
    tier: int
    carry_after: int


def classify(content: str, carry_before: int) -> PrivacyDecision:
    """Classify one user turn without any provider or persistent state."""
    try:
        if STRONG.search(content):
            return PrivacyDecision(True, 2, CARRY_TURNS)
        if MODERATE.search(content):
            if carry_before > 0:
                return PrivacyDecision(True, 1, CARRY_TURNS)
            return PrivacyDecision(False, 1, 0)
        if carry_before > 0:
            return PrivacyDecision(True, 0, carry_before - 1)
    except Exception:
        pass
    return PrivacyDecision(False, 0, 0)
