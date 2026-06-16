"""Knowledge builder -- Phase 4 Layer 2, Step 2b.

Drives the extractor on the heartbeat. Each pass:
  - finds source records not yet processed for a notebook (via a per-notebook
    cursor of processed source keys),
  - runs ONE throttled extraction batch through cognition.knowledge_extraction,
  - persists the resulting entity/relation candidates to the notebook store,
  - advances the cursor over the batch.

Contracts mirrored from semantic_recall.reindex:
  - Off the chat path; runs from the heartbeat in small throttled batches.
  - Gated by KNOWLEDGE_ENABLED (default off); a no-op when disabled.
  - Degrades silently -- never raises into the heartbeat loop.
  - On a model/infra failure (extract_from_turns returns None) the cursor is
    NOT advanced, so those turns are retried on the next beat instead of lost.

De-dup is deterministic-id + alias only for now; e5 fuzzy entity merge is
deferred until real duplicate sprawl shows up in the notebooks (earn it).

Notebooks:
  - relational  <- conversation turns (facts about Elliot and his world)
  - interior    <- her own reflections + curiosity findings (her inner wall)
"""
import json
import os
from pathlib import Path

from config.settings import (
    KNOWLEDGE_ENABLED,
    KNOWLEDGE_DIR,
    CONVERSATION_PATH,
    REFLECTIONS_PATH,
    FINDINGS_PATH,
)
from cognition.knowledge_extraction import extract_from_turns, persist_candidates
from utils.logger import log, warning


# Source records pulled into a single extraction pass per notebook per beat.
# Small on purpose: keeps each NIM call cheap and the backlog draining steadily,
# the same way RECALL_INDEX_BATCH throttles the recall indexer.
BUILD_BATCH = 12

NOTEBOOKS = ("relational", "interior")


# -- jsonl + cursor helpers ------------------------------------------------
def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return out


def _cursor_path(notebook: str) -> Path:
    return KNOWLEDGE_DIR / f"{notebook}_cursor.json"


def _load_cursor(notebook: str) -> set[str]:
    """Set of source keys already extracted for this notebook."""
    path = _cursor_path(notebook)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        return set(data.get("processed", []))
    except Exception:
        return set()


def _save_cursor(notebook: str, processed: set[str]) -> None:
    """Atomic tmp->rename write of the processed-key set. Best effort."""
    try:
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cursor_path(notebook)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"processed": sorted(processed)}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:
        warning(f"knowledge_builder/save_cursor[{notebook}]: {type(exc).__name__}: {exc}")


# -- source loaders --------------------------------------------------------
# Each item is shaped for extract_from_turns: it needs an "id" (provenance key)
# and a "content"/"role" the prompt formatter understands. "key" == "id" and is
# what the cursor tracks.
def _pending(notebook: str, processed: set[str]) -> list[dict]:
    items: list[dict] = []

    if notebook == "relational":
        for e in _read_jsonl(CONVERSATION_PATH):
            key = e.get("id")
            text = (e.get("content") or "").strip()
            if not key or key in processed or not text:
                continue
            items.append({
                "key": key,
                "id": key,
                "role": e.get("role", ""),
                "content": text,
                "ts": e.get("ts", ""),
            })
        return items

    # interior: her reflections + what her curiosity turned up
    for e in _read_jsonl(REFLECTIONS_PATH):
        ts = e.get("ts", "")
        text = (e.get("text") or "").strip()
        if not ts or not text:
            continue
        key = f"refl:{ts}"
        if key in processed:
            continue
        items.append({
            "key": key,
            "id": key,
            "role": "reflection",
            "content": text,
            "ts": ts,
        })

    for e in _read_jsonl(FINDINGS_PATH):
        ts = e.get("ts", "")
        query = (e.get("query") or "").strip()
        if not ts or not query:
            continue
        key = f"find:{ts}"
        if key in processed:
            continue
        titles = [(r.get("title") or "").strip() for r in (e.get("results") or [])]
        titles = [t for t in titles if t][:3]
        content = f"Curiosity: {query}"
        if titles:
            content += ". Found: " + "; ".join(titles)
        items.append({
            "key": key,
            "id": key,
            "role": "finding",
            "content": content,
            "ts": ts,
        })

    return items


# -- the pass --------------------------------------------------------------
async def _run_one(notebook: str, client, batch: int) -> int:
    processed = _load_cursor(notebook)
    pending = _pending(notebook, processed)
    if not pending:
        return 0

    chunk = pending[:batch]
    result = await extract_from_turns(chunk, client, notebook=notebook)
    if result is None:
        # Model/infra unavailable -- leave the cursor untouched and retry next
        # beat. Do NOT mark these turns processed.
        return 0

    entities, relations = result
    n_ent = n_rel = 0
    if entities or relations:
        n_ent, n_rel = persist_candidates(notebook, entities, relations)

    # The model replied (even if it found nothing) -- these turns are done.
    for item in chunk:
        processed.add(item["key"])
    _save_cursor(notebook, processed)

    log(
        "knowledge_builder", "built",
        notebook=notebook, batch=len(chunk),
        entities=n_ent, relations=n_rel,
        remaining=len(pending) - len(chunk),
    )
    return len(chunk)


async def run(client, *, notebook: str | None = None, batch: int | None = None) -> int:
    """Run one throttled build pass. Returns source records processed this pass.

    Gated by KNOWLEDGE_ENABLED. Never raises -- a failure in one notebook is
    logged and the other still runs.
    """
    if not KNOWLEDGE_ENABLED:
        return 0
    limit = batch if batch is not None else BUILD_BATCH
    targets = [notebook] if notebook else list(NOTEBOOKS)
    total = 0
    for nb in targets:
        try:
            total += await _run_one(nb, client, limit)
        except Exception as exc:
            warning(f"knowledge_builder/run[{nb}]: {type(exc).__name__}: {exc}")
    return total


# -- offline self-test -----------------------------------------------------
# Runs with no NIM / e5 / httpx: the extractor is monkeypatched to a canned
# async result, sources + stores + cursor are redirected to a temp dir.
if __name__ == "__main__":
    import asyncio
    import tempfile
    import memory.knowledge_store as ks

    def _seed(path: Path, records: list[dict]) -> None:
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    async def _main() -> None:
        global KNOWLEDGE_ENABLED, KNOWLEDGE_DIR, CONVERSATION_PATH, extract_from_turns
        d = Path(tempfile.mkdtemp(prefix="kb_test_"))
        conv = d / "conversation.jsonl"

        # Redirect module globals into the temp sandbox.
        KNOWLEDGE_ENABLED = True
        KNOWLEDGE_DIR = d
        CONVERSATION_PATH = conv
        ks._NOTEBOOK_PATHS["relational"] = (d / "rel_ent.jsonl", d / "rel_rel.jsonl")

        _seed(conv, [
            {"id": "user_1", "role": "user", "content": "I grew up in a poor part of town.", "ts": "t1"},
            {"id": "assistant_1", "role": "assistant", "content": "That shaped a lot.", "ts": "t2"},
            {"id": "user_2", "role": "user", "content": "My sister Maya still lives there.", "ts": "t3"},
        ])

        async def _fake_ok(turns, client, *, notebook="relational"):
            keys = [t["id"] for t in turns]
            return (
                [{"id": "person:elliot", "type": "person", "name": "Elliot", "aliases": []}],
                [{"id": "r1", "subject_id": "person:elliot", "predicate": "grew_up_in",
                  "object_value": "poor part of town", "object_kind": "literal",
                  "confidence": 0.9, "provenance": keys, "origin": "she"}],
            )

        extract_from_turns = _fake_ok
        n1 = await run(None, notebook="relational")
        assert n1 == 3, f"expected 3 processed, got {n1}"
        assert len(ks.load_entities("relational")) == 1
        assert len(ks.load_relations("relational")) == 1
        assert _load_cursor("relational") == {"user_1", "assistant_1", "user_2"}

        # Idempotent: nothing new pending -> no-op.
        n2 = await run(None, notebook="relational")
        assert n2 == 0, f"expected 0 on rerun, got {n2}"
        assert len(ks.load_relations("relational")) == 1

        # Failure path: new turn, extractor reports infra failure (None) ->
        # cursor must NOT advance and nothing new persists.
        with open(conv, "a") as f:
            f.write(json.dumps({"id": "user_3", "role": "user", "content": "New fact here.", "ts": "t4"}) + "\n")

        async def _fake_fail(turns, client, *, notebook="relational"):
            return None

        extract_from_turns = _fake_fail
        n3 = await run(None, notebook="relational")
        assert n3 == 0, f"expected 0 on infra failure, got {n3}"
        assert "user_3" not in _load_cursor("relational"), "cursor advanced on failure!"
        assert len(ks.load_relations("relational")) == 1

        # Recovery: extractor healthy again -> the held-back turn gets processed.
        extract_from_turns = _fake_ok
        n4 = await run(None, notebook="relational")
        assert n4 == 1, f"expected 1 on recovery, got {n4}"
        assert "user_3" in _load_cursor("relational")

        print("OK")

    asyncio.run(_main())
