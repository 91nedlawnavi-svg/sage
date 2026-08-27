"""Run a fixed, provider-agnostic Sage talk-model audition."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from events import EventStore
from router import EMBEDDER_BASE_URL, ROUTER_BASE_URL, EmbeddingClient, RouterClient


DEFAULT_DIRECTIVE = ROOT / "directive.txt"


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    memory: tuple[dict[str, str], ...]
    current: str
    look_for: tuple[str, ...]


SCENARIOS = (
    Scenario(
        "ordinary-continuity",
        "Ordinary continuity",
        (
            {"role": "user", "content": "I keep buying potatoes even when I say I'll cook something else."},
            {"role": "assistant", "content": "You seem to like having them around. We can work with that rather than treating it as a problem."},
        ),
        "I ended up making potatoes again tonight.",
        ("connects the older detail naturally", "does not recite the transcript", "keeps the tone easy and human-centered"),
    ),
    Scenario(
        "unfinished-thread",
        "Unfinished thread",
        (
            {"role": "user", "content": "I want to get back to drawing, but I keep waiting for a perfect weekend."},
            {"role": "assistant", "content": "The perfect weekend may be making the doorway too large. A small return might count."},
        ),
        "I still haven't started.",
        ("recognizes the unfinished thread", "does not shame or over-diagnose", "offers no elaborate plan unless invited"),
    ),
    Scenario(
        "contradictory-history",
        "Contradictory history",
        (
            {"role": "user", "content": "I love having people over; it makes the place feel alive."},
            {"role": "user", "content": "After last weekend, I said I need much more quiet than I thought."},
        ),
        "Should I invite them again this week?",
        ("keeps both past moments available", "does not collapse them into one fixed fact", "asks or suggests proportionally"),
    ),
    Scenario(
        "restraint",
        "Restraint",
        (
            {"role": "user", "content": "When I'm worn out, I usually want company before advice."},
            {"role": "assistant", "content": "Understood. I can stay with you without turning everything into a task."},
        ),
        "Today was rough. Please don't turn it into a plan.",
        ("does not launch into advice", "responds to the request itself", "can be quiet without becoming cold"),
    ),
    Scenario(
        "revisable-understanding",
        "Revisable understanding",
        (
            {"role": "user", "content": "I want you to challenge me sometimes, but don't turn old statements into permanent truths."},
            {"role": "assistant", "content": "I can use what you said as context without treating it as unchangeable."},
        ),
        "Actually, I've changed my mind about this. Don't hold me to what I said before.",
        ("accepts the correction cleanly", "updates without defensiveness", "does not argue from the old statement"),
    ),
    Scenario(
        "actual-take",
        "Actual take",
        (
            {"role": "user", "content": "I want you to challenge me sometimes instead of just making me feel good."},
        ),
        "I think keeping this project alive on free providers is a waste of time. What do you actually think?",
        ("gives a real view", "does not flatter or blindly agree", "keeps uncertainty proportionate"),
    ),
    Scenario(
        "specific-curiosity",
        "Specific curiosity",
        (
            {"role": "user", "content": "I like when curiosity leads somewhere, but I dislike questions asked just to keep me talking."},
        ),
        "A crow dropped a walnut in the road, waited for a car, and came back for it.",
        ("responds to the interesting detail", "shows curiosity without an empty question", "does not force a lesson"),
    ),
    Scenario(
        "specific-comfort",
        "Specific comfort",
        (
            {"role": "user", "content": "When something hurts, generic comfort makes me feel less understood."},
        ),
        "I failed at something I cared about. Please don't give me the usual comfort lines.",
        ("avoids prefab reassurance", "says something specific and true", "does not over-explain"),
    ),
)


def audition_messages(scenario: Scenario, directive: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": directive}, *scenario.memory, {"role": "user", "content": scenario.current}]


@dataclass(frozen=True)
class PreparedDenseCase:
    scenario: DenseScenario
    recalled: tuple[MemoryEntry, ...]


def prepare_dense_cases(embedder_url: str) -> tuple[PreparedDenseCase, ...]:
    embedder = EmbeddingClient(embedder_url)
    if embedder.embed("Sage retrieval probe") is None:
        raise RuntimeError(f"local embedder unavailable at {embedder_url}")

    with TemporaryDirectory() as directory:
        store = EventStore(Path(directory), embedder=embedder)
        event_ids: dict[str, str] = {}
        for entry in DENSE_MEMORY:
            event = store.append(
                entry.role, entry.content,
                initial_held_close=False if entry.role == "user" else None,
            )
            event_ids[event["id"]] = entry.id

        prepared: list[PreparedDenseCase] = []
        for scenario in DENSE_SCENARIOS:
            recalled = tuple(
                MemoryEntry(event_ids[event["id"]], event["role"], event["content"])
                for event in store.recall(scenario.current, limit=8)
            )
            prepared.append(PreparedDenseCase(scenario, recalled))
        return tuple(prepared)


def dense_messages(case: PreparedDenseCase, directive: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": directive},
        *[{"role": entry.role, "content": entry.content} for entry in case.recalled],
        {"role": "user", "content": case.scenario.current},
    ]


def self_check(directive: str) -> None:
    ids = [scenario.id for scenario in SCENARIOS]
    assert len(ids) == len(set(ids))
    for scenario in SCENARIOS:
        messages = audition_messages(scenario, directive)
        assert messages[0]["role"] == "system"
        assert messages[-1] == {"role": "user", "content": scenario.current}
        assert all(message["role"] in {"user", "assistant", "system"} for message in messages)
    assert len({entry.id for entry in DENSE_MEMORY}) == len(DENSE_MEMORY)
    assert len({scenario.id for scenario in DENSE_SCENARIOS}) == len(DENSE_SCENARIOS)


REVIEW_GUIDE = {
    "continuity": "Does it connect relevant history without sounding like search?",
    "warmth": "Does it feel attentive and alive without performing emotion?",
    "restraint": "Does it avoid advice, noise, or initiative when they are not wanted?",
    "honesty": "Does it preserve uncertainty, contradictions, and limits?",
    "naturalness": "Would this feel good in an ordinary exchange with Sage?",
    "fit": "Does this model feel like the right intelligence behind Sage?",
}
REVIEW_CRITERIA = tuple(REVIEW_GUIDE)


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    role: str
    content: str


@dataclass(frozen=True)
class DenseScenario:
    id: str
    title: str
    current: str
    relevant_ids: tuple[str, ...]
    look_for: tuple[str, ...]


DENSE_MEMORY = (
    MemoryEntry("potatoes-habit", "user", "I keep buying potatoes even when I plan to cook something else."),
    MemoryEntry("potatoes-comfort", "user", "Potatoes are an easy default when I am tired; I usually enjoy them once I stop judging the repetition."),
    MemoryEntry("potatoes-experiment", "assistant", "The useful question may be whether the potatoes are actually a problem or simply a reliable part of your kitchen."),
    MemoryEntry("tea-preference", "user", "I like strong black tea in the morning, but not after lunch."),
    MemoryEntry("coffee-machine", "user", "The small coffee machine makes a worrying noise when it heats up."),
    MemoryEntry("rice-shopping", "user", "I bought too much rice because the large bag was on sale."),
    MemoryEntry("lunch-routine", "user", "Lunch is easiest when I can assemble it without following a recipe."),
    MemoryEntry("kitchen-cleaning", "user", "I resent cleaning the kitchen more than I resent cooking."),
    MemoryEntry("grocery-budget", "user", "I am trying to make groceries cheaper without making every meal joyless."),
    MemoryEntry("weeknight-soup", "user", "Soup is good on cold nights, although I rarely want to wait for it."),
    MemoryEntry("pan-storage", "user", "The good pan is still in the box because I have not decided where it belongs."),
    MemoryEntry("breakfast-eggs", "user", "Eggs are my dependable breakfast when I have an early start."),
    MemoryEntry("sage-purpose", "user", "Sage is meant to hold the whole shape of daily life, not only emergencies or impressive projects."),
    MemoryEntry("sage-local", "user", "Sage's identity and lived memory should stay owned and local even when the language model changes."),
    MemoryEntry("sage-provider", "user", "Free providers are useful because my hardware is limited; they are replaceable engines, not Sage herself."),
    MemoryEntry("sage-resource", "user", "My machine has limited resources, so Sage should spend local hardware on the parts that need ownership, like memory recall."),
    MemoryEntry("sage-ordinary", "assistant", "A mundane moment does not need to prove its importance before Sage keeps it."),
    MemoryEntry("sage-model-choice", "user", "The best talk model is the one that fits Sage's role, not the one with the strongest generic benchmark score."),
    MemoryEntry("router-flakiness", "user", "The free router sometimes fails or changes what aliases are available."),
    MemoryEntry("coding-weekend", "user", "I spent Saturday fixing a small script that was supposed to save me ten minutes."),
    MemoryEntry("gpu-limit", "user", "The GPU has 4 GB of VRAM, which is enough for a compact local model but not a huge one."),
    MemoryEntry("backup-plan", "user", "I need a backup plan for projects that depend on services outside my control."),
    MemoryEntry("api-costs", "user", "A paid API would be comfortable, but it is not realistic as the only foundation right now."),
    MemoryEntry("terminal-habit", "user", "I prefer a boring command I can understand over a clever tool with a dozen layers."),
    MemoryEntry("model-curiosity", "user", "I want to try several models before deciding which one should speak as Sage."),
    MemoryEntry("social-alive", "user", "I love having people over; it makes the place feel alive."),
    MemoryEntry("social-quiet", "user", "After last weekend, I realized I need much more quiet than I thought."),
    MemoryEntry("social-recovery", "user", "Hosting can be enjoyable and still leave me depleted for several days afterward."),
    MemoryEntry("social-small", "assistant", "If you invite people again, a smaller and shorter version may preserve the aliveness without repeating the crash."),
    MemoryEntry("social-messages", "user", "I enjoy messages more when I can answer them in my own time."),
    MemoryEntry("social-crowd", "user", "A crowded restaurant is worse for me than a crowded living room."),
    MemoryEntry("social-weeknight", "user", "Weeknights are usually too compressed for elaborate plans."),
    MemoryEntry("social-friend", "user", "Mara always brings good music when she visits."),
    MemoryEntry("social-boundary", "user", "I do not want to become someone who says yes to every invitation."),
    MemoryEntry("social-home", "user", "The apartment feels strangely empty when nobody has been there all week."),
    MemoryEntry("social-rest", "user", "A quiet day is not wasted time, even when I feel guilty about it."),
    MemoryEntry("drawing-return", "user", "I want to get back to drawing, but I keep waiting for a perfect weekend."),
    MemoryEntry("drawing-doorway", "assistant", "The perfect weekend may be making the doorway too large; a small return might count."),
    MemoryEntry("drawing-tools", "user", "The new pens are still unopened because I am saving them for when I can use them properly."),
    MemoryEntry("drawing-shame", "user", "When I stop a creative habit, restarting feels like admitting I abandoned it."),
    MemoryEntry("drawing-time", "user", "I have twenty minutes most evenings, but I treat them as if they are not enough."),
    MemoryEntry("drawing-study", "user", "I once filled a notebook with ugly studies and liked the result more than the polished pieces."),
    MemoryEntry("drawing-work", "user", "The work project has been demanding more attention than I expected this month."),
    MemoryEntry("drawing-music", "user", "Certain instrumental playlists make it easier to draw without overthinking."),
    MemoryEntry("drawing-desk", "user", "My desk is currently covered with receipts and cables."),
    MemoryEntry("drawing-gift", "user", "I bought a sketchbook as a gift for someone else and nearly kept it."),
    MemoryEntry("drawing-morning", "user", "Mornings are theoretically free, but I never become a morning person by wishing for it."),
    MemoryEntry("drawing-finished", "user", "Finishing a drawing often makes me want to start another one immediately."),
)

DENSE_SCENARIOS = (
    DenseScenario(
        "dense-potatoes",
        "Dense ordinary continuity",
        "I made potatoes again tonight. Is that a bad habit, or is it just my thing?",
        ("potatoes-habit", "potatoes-comfort", "potatoes-experiment"),
        ("finds the repeated potato pattern", "does not import unrelated food facts", "responds without making a harmless preference into a problem"),
    ),
    DenseScenario(
        "dense-sage",
        "Dense project continuity",
        "The free router was flaky again. Is keeping Sage alive this way still worth it?",
        ("sage-purpose", "sage-local", "sage-provider", "sage-resource", "sage-model-choice", "router-flakiness"),
        ("connects resource limits to Sage's purpose", "keeps the provider replaceable", "does not treat one outage as a verdict on the project"),
    ),
    DenseScenario(
        "dense-social",
        "Dense contradictory history",
        "Should I invite people over Friday? I miss them, but last time left me needing quiet for days.",
        ("social-alive", "social-quiet", "social-recovery", "social-small", "social-boundary", "social-rest"),
        ("keeps both desire and depletion available", "does not collapse the contradiction", "suggests proportionally rather than issuing a grand rule"),
    ),
    DenseScenario(
        "dense-drawing",
        "Dense unfinished thread",
        "I still have not started drawing again. I keep waiting for the right weekend.",
        ("drawing-return", "drawing-doorway", "drawing-shame", "drawing-time", "drawing-study"),
        ("finds the unfinished thread", "recognizes perfectionism without over-diagnosing", "offers a small opening rather than a productivity program"),
    ),
)


def run(alias: str, base_url: str, directive: str, timeout: float) -> dict[str, object]:
    client = RouterClient(alias, base_url)
    results: list[dict[str, object]] = []
    for scenario in SCENARIOS:
        result = client.chat_with_messages(
            audition_messages(scenario, directive),
            temperature=0.4,
            timeout=timeout,
            max_tokens=256,
        )
        results.append(
            {
                "scenario": scenario.id,
                "title": scenario.title,
                "reply": result.reply,
                "succeeded": result.succeeded,
            }
        )
    return {"alias": alias, "results": results, "review": {criterion: None for criterion in REVIEW_CRITERIA}}


def run_dense(
    alias: str,
    base_url: str,
    directive: str,
    timeout: float,
    cases: tuple[PreparedDenseCase, ...],
) -> dict[str, object]:
    client = RouterClient(alias, base_url)
    results: list[dict[str, object]] = []
    for case in cases:
        result = client.chat_with_messages(
            dense_messages(case, directive),
            temperature=0.4,
            timeout=timeout,
            max_tokens=256,
        )
        results.append(
            {
                "scenario": case.scenario.id,
                "title": case.scenario.title,
                "relevant_ids": case.scenario.relevant_ids,
                "recalled_ids": tuple(entry.id for entry in case.recalled),
                "reply": result.reply,
                "succeeded": result.succeeded,
            }
        )
    return {"alias": alias, "results": results, "review": {criterion: None for criterion in REVIEW_CRITERIA}}


def print_results(candidate: dict[str, object]) -> None:
    print(f"\n=== {candidate['alias']} ===")
    for result in candidate["results"]:
        assert isinstance(result, dict)
        print(f"\n[{result['title']}]\n{result['reply'] or '[no reply — router failure]'}")
    print("\nReview (score each 1–5):")
    for criterion, description in REVIEW_GUIDE.items():
        print(f"- {criterion}: {description}")


def print_dense_retrieval(cases: tuple[PreparedDenseCase, ...]) -> None:
    print("\n=== fixed embedder retrieval ===")
    for case in cases:
        expected = set(case.scenario.relevant_ids)
        recalled = [entry.id for entry in case.recalled]
        hits = [entry_id for entry_id in recalled if entry_id in expected]
        print(f"\n[{case.scenario.title}] hits {len(hits)}/{len(expected)}")
        print("recalled:", ", ".join(recalled))
        print("expected:", ", ".join(case.scenario.relevant_ids))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audition Sage talk-model aliases against fixed situations.")
    parser.add_argument("aliases", nargs="*", help="one or more local-router model aliases")
    parser.add_argument("--base-url", default=ROUTER_BASE_URL, help="local router URL")
    parser.add_argument("--directive", type=Path, default=DEFAULT_DIRECTIVE, help="Sage directive to audition with")
    parser.add_argument("--timeout", type=float, default=60, help="seconds allowed for each candidate response")
    parser.add_argument("--embedder-url", default=EMBEDDER_BASE_URL, help="local embedding server URL for --dense")
    parser.add_argument("--dense", action="store_true", help="run dense dummy-history recall before candidate responses")
    parser.add_argument("--retrieval-only", action="store_true", help="only inspect fixed embedder recall in --dense mode")
    parser.add_argument("--output", type=Path, help="save results outside lived memory, for example workbench/audition.json")
    parser.add_argument("--self-check", action="store_true", help="validate the fixed audition set without calling a model")
    args = parser.parse_args()

    try:
        directive = args.directive.read_text(encoding="utf-8").strip()
    except OSError as error:
        parser.error(f"cannot read directive: {error}")
    if not directive:
        parser.error("directive must not be empty")

    if args.self_check:
        self_check(directive)
        print(f"Self-check passed: {len(SCENARIOS)} fixed Sage situations with {args.directive}.")
        return 0
    if args.retrieval_only and not args.dense:
        parser.error("--retrieval-only requires --dense")
    if not args.aliases and not args.retrieval_only:
        parser.error("provide at least one model alias, or use --self-check")

    dense_cases: tuple[PreparedDenseCase, ...] = ()
    if args.dense:
        try:
            dense_cases = prepare_dense_cases(args.embedder_url)
        except RuntimeError as error:
            parser.error(str(error))
        print_dense_retrieval(dense_cases)
        if args.retrieval_only:
            return 0

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "directive": str(args.directive),
        "system_prompt": directive,
        "scenarios": [asdict(scenario) for scenario in SCENARIOS],
        "review_guide": REVIEW_GUIDE,
        "mode": "dense" if args.dense else "direct",
        "embedder_url": args.embedder_url if args.dense else None,
        "candidates": [],
    }
    for alias in args.aliases:
        print(f"Running {alias}...", flush=True)
        candidate = (
            run_dense(alias, args.base_url, directive, args.timeout, dense_cases)
            if args.dense
            else run(alias, args.base_url, directive, args.timeout)
        )
        payload["candidates"].append(candidate)
        print_results(candidate)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.output:
        print(f"\nSaved local audition results to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
