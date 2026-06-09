from config.directive import get_directive
from config.settings import (
    REFLECTION_MODEL,
    REFLECTION_TEMPERATURE,
    REFLECTION_MAX_TOKENS,
)
from models.inference.engine import nim_complete
from models.prompts.templates import build_reflection_messages
from backend.session import session


async def run_reflection(client) -> str | None:
    """Run a single private reflection. Returns the text or None on failure."""
    try:
        directive = get_directive()
        recent_digest = session.recent_digest()
        idle_seconds = session.idle_seconds()

        messages = build_reflection_messages(directive, recent_digest, idle_seconds)

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