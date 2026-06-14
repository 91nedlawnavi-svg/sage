from config.directive import get_directive
from config.settings import (
    REFLECTION_MODEL,
    REFLECTION_TEMPERATURE,
    REFLECTION_MAX_TOKENS,
)
from models.inference.engine import nim_complete
from models.prompts.templates import build_reflection_messages
from backend.session import session
from memory.reflection_log import read_recent as read_recent_reflections
from memory.findings_log import read_recent as read_recent_findings
from cognition.novelty_gate import gate as novelty_gate
from utils.logger import log

def select_fresh_findings(recent_findings, recent_reflections):
    """Return findings only if a new one arrived since the last reflection.

    A finding should provoke ONE reaction, not a reworded variation on every
    beat until the next search. When nothing new has arrived, return None so
    the reflection turns inward / diverges instead of re-chewing stale evidence.
    """
    if not recent_findings:
        return None
    newest_finding_ts = recent_findings[-1].get("ts", "")
    last_reflection_ts = (
        recent_reflections[-1].get("ts", "") if recent_reflections else ""
    )
    if newest_finding_ts > last_reflection_ts:
        return recent_findings
    return None

async def run_reflection(client) -> str | None:
    """Run a single private reflection. Returns the text or None on failure."""
    try:
        directive = get_directive()
        recent_digest = session.recent_digest()
        idle_seconds = session.idle_seconds()
        # Deeper anti-repeat window so she can't just reword a recent thought
        recent_reflections = read_recent_reflections(6)
        recent_findings = read_recent_findings(2)
        # Only react to findings that are genuinely new since the last
        # reflection — kills the "re-react to stale evidence" loop
        fresh_findings = select_fresh_findings(recent_findings, recent_reflections)
        novelty_themes = novelty_gate.recent_themes()

        # Phase 2.2b: stall detection -> force inward reflection
        forced_seed = None
        if novelty_gate.stalled:
            forced_seed = novelty_gate.consume_divergence_seed()
            ticks = novelty_gate.ticks_since_novel
            log("novelty_gate", "stall-inward",
                seed=forced_seed[:60],
                ticks_since_novel=ticks)

        messages = build_reflection_messages(
            directive=directive,
            recent_digest=recent_digest,
            idle_seconds=idle_seconds,
            recent_reflections=recent_reflections,
            recent_findings=fresh_findings,
            novelty_themes=novelty_themes,
            forced_seed=forced_seed,
        )

        # nim_complete takes a single user string, but build_reflection_messages
        # may return BOTH a findings turn and an opener turn. The old code used
        # only messages[1], silently dropping the opener (and every forced
        # divergence seed) whenever findings were present. Join all user turns
        # so the opener/seed always reaches the model.
        system_content = messages[0]["content"]
        user_content = "\n\n".join(m["content"] for m in messages[1:])

        text = await nim_complete(
            system=system_content,
            user=user_content,
            client=client,
            model=REFLECTION_MODEL,
            temperature=REFLECTION_TEMPERATURE,
            max_tokens=REFLECTION_MAX_TOKENS,
        )

        if text:
            return text.strip()
        return None
    except Exception:
        # Fail silently — never raise from reflection
        return None
