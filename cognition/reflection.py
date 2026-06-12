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


async def run_reflection(client) -> str | None:
    """Run a single private reflection. Returns the text or None on failure."""
    try:
        directive = get_directive()
        recent_digest = session.recent_digest()
        idle_seconds = session.idle_seconds()
        # Pull recent reflections for anti-repeat / continuity
        recent_reflections = read_recent_reflections(3)
        # Pull recent findings to feed curiosity
        recent_findings = read_recent_findings(2)
        # Check novelty gate for circling topics
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
            recent_findings=recent_findings,
            novelty_themes=novelty_themes,
            forced_seed=forced_seed,
        )

        text = await nim_complete(
            system=messages[0]["content"],
            user=messages[1]["content"],
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