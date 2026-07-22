"""Stance extraction — Wave 3, Blueprint §2.5–§2.6.

The quiet-time job that digests her OWN stream (interior episodes:
reflections, readings, findings) into the belief ledger:
  interior episodes → stance events (→ beliefs, via record_stance)
and, for readings, a source-trust verdict (substance | slop) → §2.6 ledger.

This is the "her own pipeline writes it constantly" from the write-path
doctrine in interior_api — before this module, record_stance had no
production caller and beliefs could only come from prompt cosplay.

Contracts (same discipline as consolidation.py):
- Off the chat path; heartbeat quiet slot; 90s leash outside.
- Degrades to no-op on any failure; never raises upward.
- The LLM proposes; this module disposes: stances missing fields are
  dropped, at most MAX_STANCES_PER_PASS land per pass (no belief spam),
  source verdicts only for domains actually present in the batch.
- Interior store only — never touches relational (contamination wall).
"""
from __future__ import annotations

import json
import re
import urllib.parse

from memory import interior_api
from utils.logger import log, warning

# Episodes per pass. Reflections run ~1.5k chars; 6 keeps one reasoning call
# comfortably inside the 90s leash. Backlog drains across beats.
EXTRACT_BATCH = 6
MAX_STANCES_PER_PASS = 3

_SYSTEM = """You are the stance-formation function of Sage's mind. You read \
her recent private stream — reflections, things she read, search findings — \
and decide whether anything genuinely MOVED her on a topic.

A stance event is sediment, not a hot take: something she read or thought \
that shifted her lean on a topic she could hold an opinion about (ideas, \
ethics, aesthetics, politics, how minds work — anything). Most episodes move \
nothing; an empty list is the usual answer.

Rules:
- topic: short kebab-case slug, stable across time (e.g. "animal-cognition", \
"attention-economy"). Reuse the obvious slug, don't invent synonyms.
- direction: a short hyphenated lean, e.g. "corvids-have-culture", \
"skeptical-of-longtermism".
- why: one sentence, concrete, citing what in the episode moved her.
- is_steelman: true ONLY if the episode records her engaging the strongest \
OPPOSING case on a topic she already leans on.
- For episodes marked [reading], also judge the source: "substance" (taught \
her something real) or "slop" (padding, listicle, said nothing).
- At most 2 stances per reply. No stance for mere curiosity or topic-mention \
— only for movement.
- Reply with ONLY a JSON object, no prose: \
{"stances": [{"topic": str, "direction": str, "why": str, \
"source": str|null, "is_steelman": bool}], \
"source_verdicts": [{"domain": str, "verdict": "substance"|"slop"}]}"""


def _domain(source_key: str | None) -> str | None:
    """read:https://host/path → host (reading episodes store URL in source_key)."""
    if not source_key or not source_key.startswith("read:"):
        return None
    try:
        host = urllib.parse.urlparse(source_key[5:]).netloc.lower()
        return host or None
    except Exception:
        return None


def _build_prompt(episodes: list[dict]) -> str:
    lines = ["HER RECENT STREAM (this batch):"]
    for e in episodes:
        dom = _domain(e.get("source_key"))
        tag = f"[{e['source']}" + (f" from {dom}" if dom else "") + "]"
        lines.append(f"- ts={e['ts']} {tag} {e['content']}")
    return "\n".join(lines)


def _parse(raw: str) -> dict | None:
    """Tolerant parse: strip think-preamble and fences, then strict JSON."""
    if not raw:
        return None
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    out = {"stances": [], "source_verdicts": []}
    for s in obj.get("stances") or []:
        if (isinstance(s, dict)
                and isinstance(s.get("topic"), str) and s["topic"].strip()
                and isinstance(s.get("direction"), str) and s["direction"].strip()
                and isinstance(s.get("why"), str) and s["why"].strip()):
            out["stances"].append(s)
    for v in obj.get("source_verdicts") or []:
        if (isinstance(v, dict) and isinstance(v.get("domain"), str)
                and v.get("verdict") in ("substance", "slop")):
            out["source_verdicts"].append(v)
    return out


async def run(client) -> int:
    """One quiet-slot pass. Returns episodes digested (0 = idle/infra-down)."""
    try:
        episodes = interior_api.pending_episodes(limit=EXTRACT_BATCH)
        if not episodes:
            return 0

        from models.inference.engine import nim_complete
        raw = await nim_complete(_SYSTEM, _build_prompt(episodes), client,
                                 temperature=0.2, max_tokens=2048)
        if raw is None:
            return 0  # infra down — retry next quiet slot, episodes stay pending
        parsed = _parse(raw)
        if parsed is None:
            warning("stance_extraction: malformed model output; will retry",
                    preview=(raw or "")[:200])
            return 0

        # Verdicts only for domains actually in the batch (no fabricated trust).
        batch_domains = {d for e in episodes if (d := _domain(e.get("source_key")))}
        n_stance = n_verdict = 0
        for s in parsed["stances"][:MAX_STANCES_PER_PASS]:
            topic = s["topic"].strip().lower()
            await interior_api.record_stance(
                topic=topic,
                direction=s["direction"].strip(),
                why=s["why"].strip(),
                source=(s.get("source") or None),
                is_steelman=bool(s.get("is_steelman")),
            )
            n_stance += 1
        for v in parsed["source_verdicts"]:
            dom = v["domain"].strip().lower()
            if dom not in batch_domains:
                continue
            await interior_api.record_source(dom, v["verdict"])
            n_verdict += 1

        await interior_api.mark_processed([e["id"] for e in episodes])
        log("stance_extraction", "pass",
            episodes=len(episodes), stances=n_stance, verdicts=n_verdict)
        return len(episodes)
    except Exception as exc:
        warning(f"stance_extraction/run: {type(exc).__name__}: {exc}")
        return 0


# ── offline self-test ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio
    import tempfile
    from pathlib import Path
    import memory.sqlite_core as core

    async def _main():
        d = Path(tempfile.mkdtemp(prefix="stance_extraction_test_"))
        core._DB_PATHS = {s: d / f"{s}.db" for s in core.STORES}
        core._writers = {}

        await interior_api.add_episode(
            source="reflection", content="The crows thing won't leave me alone.",
            source_key="refl:1")
        await interior_api.add_episode(
            source="reading", content="[Reading: Corvid culture]\nlong article",
            source_key="read:https://example.org/corvids")

        async def _fake_nim(system, user, client, **kw):
            assert "example.org" in user, "reading domain missing from prompt"
            return json.dumps({
                "stances": [
                    {"topic": "Animal-Cognition", "direction": "corvids-have-culture",
                     "why": "the funeral behavior evidence", "source": "example.org",
                     "is_steelman": False},
                    {"topic": "", "direction": "x", "why": "y"},  # invalid — dropped
                ],
                "source_verdicts": [
                    {"domain": "example.org", "verdict": "substance"},
                    {"domain": "notinbatch.com", "verdict": "slop"},  # fabricated — dropped
                ],
            })

        import models.inference.engine as eng
        real_nim = eng.nim_complete
        eng.nim_complete = _fake_nim
        try:
            n = await run(None)
            assert n == 2, f"expected 2 digested, got {n}"
            b = interior_api.get_belief("animal-cognition")
            assert b is not None and b["direction"] == "corvids-have-culture", b
            assert interior_api.source_trust("example.org")["substance"] == 1
            assert interior_api.source_trust("notinbatch.com") is None, \
                "fabricated verdict landed"
            assert interior_api.pending_episodes() == [], "batch not marked processed"

            # malformed output degrades: episodes stay pending
            await interior_api.add_episode(source="reflection", content="more",
                                           source_key="refl:2")
            async def _fake_bad(system, user, client, **kw):
                return "not json at all"
            eng.nim_complete = _fake_bad
            assert await run(None) == 0
            assert len(interior_api.pending_episodes()) == 1, \
                "episodes lost on malformed output"
        finally:
            eng.nim_complete = real_nim

        print("OK stance_extraction self-test")

    asyncio.run(_main())
