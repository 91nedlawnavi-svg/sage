from config.settings import (
    CHAT_MODEL,
    CHAT_API_URL,
    NVIDIA_API_KEY,
)
from models.inference.engine import nim_complete
import httpx


async def extract_query(reflection_text: str, client: httpx.AsyncClient) -> str | None:
    """Extract a search query from a reflection if it contains genuine curiosity.
    Returns the query string or None."""
    if not reflection_text or not reflection_text.strip():
        return None

    system = (
        "You are an extractor. Your only job: read a private reflection and "
        "decide if it contains a genuine curiosity that a web search could "
        "meaningfully inform. If YES, output ONE concise search query and "
        "nothing else. If NO, output exactly: NONE"
    )

    user = f"Reflection:\n{reflection_text.strip()}\n\nQuery or NONE:"

    try:
        text = await nim_complete(
            system=system,
            user=user,
            client=client,
            model=CHAT_MODEL,
            temperature=0.3,
            max_tokens=64,
        )
        if text:
            text = text.strip()
            if text.upper() != "NONE" and len(text) > 3:
                return text
        return None
    except Exception:
        return None