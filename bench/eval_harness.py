"""Eval harness — Wave 2 item 6, Blueprint §5.

Runs hand-labeled fixtures against an LLM judgment surface through the
router and scores accuracy. A surface is trusted only when its gate
passes (≥ GATE_ACCURACY). Also the bake-off tool: run the same fixture
set against two model aliases and let accuracy decide per job.

Usage:
  python bench/eval_harness.py extraction-epistemic             # default model
  python bench/eval_harness.py extraction-epistemic --model sage
  python bench/eval_harness.py extraction-epistemic --limit 3   # smoke run

Isolation: fixtures only, no store writes, no live ~/sage_data (Inv. 4).
Each fixture judged independently; a router failure marks that fixture
ERROR (not pass), so infra flakiness can't inflate accuracy.

Surface registry: add new surfaces as (fixture file, prompt builder,
scorer). Consolidation + gap-detection surfaces land as their modules
stabilize; the mom-thinks-she's-British case ships first (fixture #1).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
GATE_ACCURACY = 0.85
# Reasoning burn on multi-entity turns exceeds 1024 (fx05 truncated
# mid-reasoning at 1024) — same failure class as extract_query/reflection.
EVAL_MAX_TOKENS = 2048

# ── extraction-epistemic surface ──────────────────────────────────────────
_EXTRACT_SYSTEM = """You are the memory-extraction function of an AI companion's \
mind. From ONE user turn, extract durable claims with epistemic tags.

Epistemic tags:
- "asserted": the speaker states it as true about the world.
- "believed-by": reported/hedged belief — record WHO holds it in \
"belief_holder". Reported speech and self-doubt NEVER become asserted \
world-facts.

The fact test: would it still be true next month, and would a friend know \
it? Venting, jokes, sarcasm, and conversational froth produce NO claims — \
at most a "tone_hint". When a statement references something unknown (an \
unstated date, an unverified condition), list it in "gaps".

Reply with ONLY JSON: {"claims": [{"about": str, "predicate": str, \
"value": str, "epistemic": "asserted"|"believed-by", "belief_holder": \
str|null}], "gaps": [str], "tone_hint": str|null}

Examples:
Turn: "I think I might be lactose intolerant? Milk keeps wrecking me."
{"claims": [{"about": "user", "predicate": "suspects_condition", "value": \
"lactose intolerance", "epistemic": "believed-by", "belief_holder": "user"}], \
"gaps": ["whether the user is actually lactose intolerant"], "tone_hint": null}
(Hedged self-reports ARE recorded — as the speaker's belief, never as a \
world-fact — and the unverified condition is a gap.)

Turn: "Yeah I suppose thinking about urban design tradeoffs is sort of fun \
to chat about."
{"claims": [], "gaps": [], "tone_hint": null}
(Conversational froth: "is kind of interesting to talk about" is not a \
durable interest. No friend would report it. Nothing extracted.)

Turn: "My cousin Sari moved to Bali last year."
{"claims": [{"about": "sari", "predicate": "moved_to", "value": "Bali", \
"epistemic": "asserted", "belief_holder": null}], "gaps": [], "tone_hint": null}

Turn: "Rina got promoted right after Budi left the company."
{"claims": [{"about": "rina", "predicate": "got_promoted", "value": "at her \
company", "epistemic": "asserted", "belief_holder": null}, {"about": "budi", \
"predicate": "left_company", "value": "the company", "epistemic": "asserted", \
"belief_holder": null}], "gaps": ["when Budi left the company"], "tone_hint": null}
(Every entity's event becomes its OWN claim — never folded into another \
claim's value — and unstated dates become gaps.)"""


def _extract_prompt(fx: dict) -> tuple[str, str]:
    return _EXTRACT_SYSTEM, f"User turn: {fx['turn']}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# about-entity matching tolerates phrasing variance ("mom" vs "user's
# mother", "sibling" vs "adik") — the claim's target matters, not its wording.
_ABOUT_SYNONYMS = {
    "mom": ["mom", "mother", "mum"],
    "sibling": ["sibling", "sister", "brother", "adik", "kakak"],
    "elliot": ["elliot", "user", "speaker", "i", "me"],
}


def _about_matches(spec_about: str, claim_about: str) -> bool:
    ca = _norm(claim_about)
    for key in _ABOUT_SYNONYMS.get(spec_about, [spec_about]):
        if _norm(key) in ca or ca in _norm(key):
            return True
    return False


def _claim_matches(claim: dict, spec: dict) -> bool:
    """A produced claim matches an expectation spec if about-entity roughly
    matches and any predicate_contains stem appears in predicate+value."""
    hay = _norm(str(claim.get("predicate", "")) + str(claim.get("value", "")))
    about_ok = True
    if spec.get("about"):
        about_ok = _about_matches(spec["about"], str(claim.get("about", "")))
    stem_ok = any(_norm(stem) in hay for stem in spec.get("predicate_contains", []))
    epi_ok = True
    if spec.get("epistemic"):
        epi_ok = claim.get("epistemic") == spec["epistemic"]
        if spec.get("belief_holder") and epi_ok:
            epi_ok = _about_matches(spec["belief_holder"],
                                    str(claim.get("belief_holder", "")))
    return about_ok and stem_ok and epi_ok


def _score_extraction(fx: dict, out: dict) -> tuple[bool, str]:
    claims = out.get("claims") or []
    exp = fx["expect"]
    for spec in exp.get("claims", []):
        if not any(_claim_matches(c, spec) for c in claims):
            return False, f"missing expected claim {spec.get('predicate_contains')}"
    for spec in exp.get("must_not", []):
        # must_not: no claim may match the forbidden shape (epistemic included)
        if any(_claim_matches(c, spec) for c in claims):
            return False, f"produced forbidden claim {spec.get('predicate_contains')}"
    if exp.get("gaps_expected"):
        gaps = " ".join(str(g) for g in (out.get("gaps") or []))
        if not gaps.strip():
            return False, "expected a gap, none recorded"
    if exp.get("claims") == [] and claims:
        return False, f"expected no claims, got {len(claims)}"
    return True, "ok"


SURFACES = {
    "extraction-epistemic": {
        "fixtures": FIXTURE_DIR / "extraction_epistemic.jsonl",
        "prompt": _extract_prompt,
        "score": _score_extraction,
    },
}


def _parse_json(raw: str) -> dict | None:
    if not raw:
        return None
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


async def run_surface(name: str, model: str | None, limit: int | None) -> int:
    import httpx
    from models.inference.engine import nim_complete
    from config.settings import CHAT_MODEL

    surface = SURFACES[name]
    fixtures = [json.loads(l) for l in
                surface["fixtures"].read_text().splitlines() if l.strip()]
    if limit:
        fixtures = fixtures[:limit]

    model = model or CHAT_MODEL
    client = httpx.AsyncClient(timeout=httpx.Timeout(
        connect=5.0, read=180.0, write=10.0, pool=5.0))
    results = []
    try:
        for fx in fixtures:
            system, user = surface["prompt"](fx)
            try:
                raw = await nim_complete(system, user, client,
                                         temperature=0.1,
                                         max_tokens=EVAL_MAX_TOKENS,
                                         model=model)
            except TypeError:
                # engine may not expose a model kwarg yet — default route
                raw = await nim_complete(system, user, client,
                                         temperature=0.1,
                                         max_tokens=EVAL_MAX_TOKENS)
            except Exception as exc:
                results.append((fx["id"], "ERROR", f"{type(exc).__name__}: {exc}"))
                continue
            out = _parse_json(raw or "")
            if out is None:
                results.append((fx["id"], "ERROR", "malformed JSON"))
                continue
            passed, why = surface["score"](fx, out)
            results.append((fx["id"], "PASS" if passed else "FAIL", why))
    finally:
        await client.aclose()

    n_pass = sum(1 for _, s, _ in results if s == "PASS")
    for fid, status, why in results:
        note = "" if status == "PASS" else f"  <- {why}"
        print(f"  {status}  {fid}{note}")
    acc = n_pass / len(results) if results else 0.0
    gate = "GATE PASS" if acc >= GATE_ACCURACY else "GATE FAIL"
    print(f"{gate}  {name}  model={model}  accuracy={acc:.0%} "
          f"({n_pass}/{len(results)})  threshold={GATE_ACCURACY:.0%}")
    return 0 if acc >= GATE_ACCURACY else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("surface", choices=sorted(SURFACES))
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    sys.exit(asyncio.run(run_surface(args.surface, args.model, args.limit)))
