# Autonomous Metabolism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sage thinks after conversations — scanning for gaps, exploring the web, digesting what she finds, and optionally leaving Elliot a note.

**Architecture:** A four-stage post-conversation pipeline (gap scan → explore → digest → reach) triggered by conversation silence, running in the heartbeat thread. Each stage gates the next; silence is the default outcome. All storage goes through existing EventStore and InteriorStore.

**Tech Stack:** Python 3 stdlib only. SearXNG via existing `search.py`. Free-tier models via existing `router.py`.

**Spec:** `docs/superpowers/specs/2026-09-02-autonomous-metabolism-design.md`

## Global Constraints

- No external dependencies. Stdlib-only Python.
- No reads from `~/sage_data/` event/reflection content in tests — use temp dirs.
- Do NOT read `src/sensitive.py` content or `~/sage_data/*.jsonl` content at any point.
- Sensitive events excluded from all metabolism input.
- `SAGE_METABOLISM_DELAY` env var controls silence threshold (default 300 seconds).
- All metabolism JSONL records go in `~/sage_data/interior/metabolism.jsonl`.
- Metabolism reflections use `category: "metabolism"`, mixed into Reflections tab.
- Tests use `FakeScribe` / `DeadRouter` patterns from `tests/test_foundation.py`.

---

### Task 1: Widen completion tracking and add metabolism storage path

**Files:**
- Modify: `src/events.py:211-240` (widen `Literal` type on `append_heartbeat_completion` and `heartbeat_completed`)
- Modify: `src/interior.py:72-78` (add `metabolism_path` in `__init__`)
- Modify: `launch.py:45` (read `SAGE_METABOLISM_DELAY` env var)
- Modify: `.env.example` (add `SAGE_METABOLISM_DELAY`)
- Test: `tests/test_foundation.py`

**Interfaces:**
- Consumes: existing `EventStore.append_heartbeat_completion`, `EventStore.heartbeat_completed`, `InteriorStore.__init__`
- Produces: `EventStore.append_heartbeat_completion(stage: "entities"|"reflection"|"metabolism", ...)`, `EventStore.heartbeat_completed(stage: "entities"|"reflection"|"metabolism")`, `InteriorStore.metabolism_path: Path`

- [ ] **Step 1: Write failing test for metabolism completion tracking**

```python
def test_metabolism_completion_tracking(self) -> None:
    self.store.append("user", "Hello")
    event = self.store.read_all()[0]
    self.store.append_heartbeat_completion("metabolism", event["id"])
    completed = self.store.heartbeat_completed("metabolism")
    self.assertIn(event["id"], completed)
```

Add this to the `FoundationTests` class in `tests/test_foundation.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_foundation.py::FoundationTests::test_metabolism_completion_tracking -v`
Expected: FAIL — the `Literal` type rejects `"metabolism"`.

- [ ] **Step 3: Widen the Literal type in events.py**

In `src/events.py`, change both signatures:

```python
# append_heartbeat_completion
def append_heartbeat_completion(
    self,
    stage: Literal["entities", "reflection", "metabolism"],
    source_event_id: str,
) -> HeartbeatCompletion:
```

```python
# heartbeat_completed
def heartbeat_completed(self, stage: Literal["entities", "reflection", "metabolism"]) -> set[str]:
```

- [ ] **Step 4: Add metabolism_path to InteriorStore.__init__**

In `src/interior.py`, inside `__init__`, after `self.identity_path`:

```python
self.metabolism_path = self.interior_dir / "metabolism.jsonl"
```

- [ ] **Step 5: Add SAGE_METABOLISM_DELAY to launch.py and .env.example**

In `launch.py`, after the `extract_router` line (around line 58), add:

```python
metabolism_delay = float(os.getenv("SAGE_METABOLISM_DELAY", "300"))
```

Pass it to Heartbeat:

```python
heartbeat = Heartbeat(store, interior, router, extract_router=extract_router, interval_seconds=120.0, metabolism_delay=metabolism_delay)
```

In `.env.example`, append:

```
# Seconds of silence before metabolism runs (default 300)
# SAGE_METABOLISM_DELAY=300
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 -m pytest tests/test_foundation.py::FoundationTests::test_metabolism_completion_tracking -v`
Expected: PASS

- [ ] **Step 7: Run full suite**

Run: `python3 -m pytest tests/ -v`
Expected: all 92 + 1 = 93 pass

- [ ] **Step 8: Commit**

```bash
git add src/events.py src/interior.py launch.py .env.example tests/test_foundation.py
git commit -m "feat: widen heartbeat completion for metabolism, add metabolism_path"
```

---

### Task 2: Metabolism pipeline — gap scan

**Files:**
- Create: `src/metabolism.py`
- Test: `tests/test_foundation.py`

**Interfaces:**
- Consumes: `EventStore.history()` → `list[Event]`, `RouterClient.chat_with_messages()`, `InteriorStore.metabolism_path`
- Produces: `gap_scan(events: list[dict], router: RouterClient, interior: InteriorStore, source_event_id: str) -> list[dict]` — returns list of `{"gap": str, "query": str}` or empty list. Writes a `kind: "gap_scan"` record to `metabolism.jsonl`.

- [ ] **Step 1: Write failing tests for gap_scan**

Add to `FoundationTests` in `tests/test_foundation.py`:

```python
def test_gap_scan_returns_empty_on_no_gaps(self) -> None:
    from metabolism import gap_scan
    scribe = FakeScribe("[]")
    result = gap_scan(
        [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}],
        scribe,
        self.interior,
        "evt-1",
    )
    self.assertEqual(result, [])
    # No metabolism record written for empty scan
    self.assertFalse(self.interior.metabolism_path.exists())

def test_gap_scan_returns_gaps_and_writes_record(self) -> None:
    from metabolism import gap_scan
    scribe = FakeScribe('[{"gap": "What is WIB timezone offset?", "query": "WIB timezone UTC offset"}]')
    result = gap_scan(
        [{"role": "user", "content": "What time is it in WIB?"}, {"role": "assistant", "content": "I'm not sure of the exact offset."}],
        scribe,
        self.interior,
        "evt-2",
    )
    self.assertEqual(len(result), 1)
    self.assertEqual(result[0]["gap"], "What is WIB timezone offset?")
    # Record written
    self.assertTrue(self.interior.metabolism_path.exists())
    import json
    records = [json.loads(line) for line in self.interior.metabolism_path.read_text().splitlines()]
    self.assertEqual(records[0]["kind"], "gap_scan")
    self.assertEqual(records[0]["source_event_id"], "evt-2")

def test_gap_scan_returns_empty_on_router_failure(self) -> None:
    from metabolism import gap_scan
    result = gap_scan(
        [{"role": "user", "content": "Hi"}],
        self.router,  # DeadRouter — always fails
        self.interior,
        "evt-3",
    )
    self.assertEqual(result, [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_foundation.py -k "gap_scan" -v`
Expected: FAIL — `metabolism` module not found.

- [ ] **Step 3: Implement gap_scan in src/metabolism.py**

Create `src/metabolism.py`:

```python
"""Autonomous metabolism pipeline — post-conversation thinking."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from interior import InteriorStore
from router import RouterClient

_log = logging.getLogger("sage.metabolism")

GAP_SCAN_PROMPT = """You are Sage, reviewing a recent conversation with Elliot. Identify 1-3 specific things from this conversation where you were uncertain, didn't know the answer, were curious, or noticed a gap in your understanding. Return a JSON list of objects: [{"gap": "description", "query": "search query"}]. If there are no genuine gaps or curiosity, return [].

Recent conversation:
{dialogue}"""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_metabolism(interior: InteriorStore, record: dict) -> None:
    interior._ensure_dir()
    with interior.metabolism_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def gap_scan(
    events: list[dict],
    router: RouterClient,
    interior: InteriorStore,
    source_event_id: str,
) -> list[dict]:
    """Scan recent conversation for knowledge gaps. Returns list of gaps or []."""
    if not events:
        return []
    dialogue = "\n".join(f"{e['role']}: {e['content']}" for e in events[-10:])
    result = router.chat_with_messages(
        [{"role": "user", "content": GAP_SCAN_PROMPT.format(dialogue=dialogue)}]
    )
    if not result.succeeded or not result.reply:
        return []
    try:
        cleaned = result.reply.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        gaps = json.loads(cleaned.strip())
    except (json.JSONDecodeError, ValueError):
        _log.warning("gap_scan returned unparseable JSON")
        return []
    if not isinstance(gaps, list) or not gaps:
        return []
    valid = [g for g in gaps if isinstance(g, dict) and g.get("gap") and g.get("query")]
    if not valid:
        return []
    _append_metabolism(interior, {
        "kind": "gap_scan",
        "id": str(uuid4()),
        "source_event_id": source_event_id,
        "said_at": _timestamp(),
        "gaps": valid,
    })
    return valid
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_foundation.py -k "gap_scan" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/metabolism.py tests/test_foundation.py
git commit -m "feat: metabolism gap scan stage"
```

---

### Task 3: Metabolism pipeline — explore

**Files:**
- Modify: `src/metabolism.py`
- Test: `tests/test_foundation.py`

**Interfaces:**
- Consumes: `gap_scan` return value (list of `{"gap": str, "query": str}`), `search()` from `search.py`, `EventStore.append()`, `InteriorStore.metabolism_path`
- Produces: `explore(gaps: list[dict], store: EventStore, interior: InteriorStore, source_event_id: str) -> list[dict]` — returns list of `{"gap": str, "query": str, "results": list[SearchResult]}` for gaps that got results. Writes exploration record to `metabolism.jsonl`. Stores search results as assistant events.

- [ ] **Step 1: Write failing tests for explore**

```python
def test_explore_searches_gaps_and_stores_events(self) -> None:
    from metabolism import explore
    from unittest.mock import patch
    from search import SearchResult
    fake_results = [SearchResult(title="WIB", snippet="UTC+7", url="https://example.com")]
    with patch("metabolism.search", return_value=fake_results):
        result = explore(
            [{"gap": "WIB offset", "query": "WIB timezone"}],
            self.store,
            self.interior,
            "evt-1",
        )
    self.assertEqual(len(result), 1)
    self.assertIn("results", result[0])
    # Check episodic event was stored
    events = self.store.read_all()
    metabolism_events = [e for e in events if "[Metabolism search:" in e["content"]]
    self.assertEqual(len(metabolism_events), 1)

def test_explore_returns_empty_when_all_searches_fail(self) -> None:
    from metabolism import explore
    from unittest.mock import patch
    with patch("metabolism.search", return_value=[]):
        result = explore(
            [{"gap": "Unknown thing", "query": "unknown query"}],
            self.store,
            self.interior,
            "evt-2",
        )
    self.assertEqual(result, [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_foundation.py -k "explore" -v`
Expected: FAIL — `explore` not found.

- [ ] **Step 3: Implement explore**

Add to `src/metabolism.py`:

```python
from search import search, format_search_context


def explore(
    gaps: list[dict],
    store: EventStore,
    interior: InteriorStore,
    source_event_id: str,
) -> list[dict]:
    """Search the web for each gap. Store results as episodic events. Returns gaps with results."""
    if not gaps:
        return []
    explored = []
    for gap in gaps[:3]:
        query = gap["query"]
        results = search(query)
        if not results:
            continue
        # Store as episodic event
        sources = "\n".join(r.url for r in results)
        content = f"[Metabolism search: {query}]\nSources: {sources}"
        store.append("assistant", content)
        explored.append({**gap, "results": [{"title": r.title, "snippet": r.snippet, "url": r.url} for r in results]})
    if not explored:
        return []
    _append_metabolism(interior, {
        "kind": "exploration",
        "id": str(uuid4()),
        "source_event_id": source_event_id,
        "said_at": _timestamp(),
        "gaps_explored": len(explored),
        "queries": [g["query"] for g in explored],
    })
    return explored
```

Add `from events import EventStore` to imports at top of `metabolism.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_foundation.py -k "explore" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/metabolism.py tests/test_foundation.py
git commit -m "feat: metabolism explore stage"
```

---

### Task 4: Metabolism pipeline — digest

**Files:**
- Modify: `src/metabolism.py`
- Test: `tests/test_foundation.py`

**Interfaces:**
- Consumes: `explore` return value, `RouterClient.chat_with_messages()`, `InteriorStore.append_reflection()`, `InteriorStore.list_reflections()`
- Produces: `digest(explored: list[dict], router: RouterClient, interior: InteriorStore, source_event_id: str) -> str | None` — returns the digest text or None. Stores a reflection with `category: "metabolism"`.

- [ ] **Step 1: Write failing tests for digest**

```python
def test_digest_creates_metabolism_reflection(self) -> None:
    from metabolism import digest
    scribe = FakeScribe("I learned that WIB is UTC+7, which connects to Elliot asking about time zones last week.")
    result = digest(
        [{"gap": "WIB offset", "query": "WIB timezone", "results": [{"title": "WIB", "snippet": "UTC+7", "url": "https://example.com"}]}],
        scribe,
        self.interior,
        "evt-1",
    )
    self.assertIsNotNone(result)
    reflections = self.interior.list_reflections(limit=100)
    metabolism_refs = [r for r in reflections if r.get("category") == "metabolism"]
    self.assertEqual(len(metabolism_refs), 1)
    self.assertEqual(metabolism_refs[0]["source_event_id"], "evt-1")

def test_digest_returns_none_on_router_failure(self) -> None:
    from metabolism import digest
    result = digest(
        [{"gap": "test", "query": "test", "results": [{"title": "t", "snippet": "s", "url": "u"}]}],
        self.router,  # DeadRouter
        self.interior,
        "evt-2",
    )
    self.assertIsNone(result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_foundation.py -k "digest" -v`
Expected: FAIL — `digest` not found.

- [ ] **Step 3: Implement digest**

Add to `src/metabolism.py`:

```python
DIGEST_PROMPT = """You are Sage, thinking privately after a conversation with Elliot. You noticed some gaps and searched for answers. Below are the gaps and what you found. Write a brief private note (2-4 sentences) connecting what you learned to the conversation. This is for your own notebook, not a message to Elliot. If the search results didn't actually resolve the gap or add anything interesting, say so honestly and keep it to one sentence.

Gaps and findings:
{findings}

Recent reflections for context:
{reflections}"""


def digest(
    explored: list[dict],
    router: RouterClient,
    interior: InteriorStore,
    source_event_id: str,
) -> str | None:
    """Synthesize exploration results into a metabolism reflection. Returns text or None."""
    if not explored:
        return None
    findings = []
    for gap in explored:
        lines = [f"Gap: {gap['gap']}"]
        for r in gap.get("results", []):
            lines.append(f"  - {r['title']}: {r['snippet']}")
        findings.append("\n".join(lines))
    recent = interior.list_reflections(limit=5)
    reflection_text = "\n".join(f"- {r['content']}" for r in recent) if recent else "(none yet)"
    prompt = DIGEST_PROMPT.format(
        findings="\n\n".join(findings),
        reflections=reflection_text,
    )
    result = router.chat_with_messages([{"role": "user", "content": prompt}])
    if not result.succeeded or not result.reply:
        return None
    text = result.reply.strip()
    if not text:
        return None
    interior.append_reflection(text, "metabolism", source_event_id=source_event_id)
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_foundation.py -k "digest" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/metabolism.py tests/test_foundation.py
git commit -m "feat: metabolism digest stage"
```

---

### Task 5: Metabolism pipeline — reach

**Files:**
- Modify: `src/metabolism.py`
- Test: `tests/test_foundation.py`

**Interfaces:**
- Consumes: `digest` return value (str), `RouterClient.chat_with_messages()`, `InteriorStore.set_waiting_message()`, `InteriorStore.metabolism_path`
- Produces: `reach(digest_text: str, router: RouterClient, interior: InteriorStore, source_event_id: str) -> bool` — returns True if a waiting message was set, False otherwise. Writes a reach record to `metabolism.jsonl`.

- [ ] **Step 1: Write failing tests for reach**

```python
def test_reach_sets_waiting_message_when_warranted(self) -> None:
    from metabolism import reach
    scribe = FakeScribe("I looked into WIB timezone offsets — turns out it's UTC+7, which means your 9am is 2am UTC.")
    result = reach("I learned WIB is UTC+7.", scribe, self.interior, "evt-1")
    self.assertTrue(result)
    msg = self.interior.get_waiting_message()
    self.assertIsNotNone(msg)
    self.assertIn("UTC+7", msg["content"])

def test_reach_no_message_when_model_declines(self) -> None:
    from metabolism import reach
    scribe = FakeScribe("NO_MESSAGE")
    result = reach("Nothing interesting found.", scribe, self.interior, "evt-2")
    self.assertFalse(result)
    self.assertIsNone(self.interior.get_waiting_message())

def test_reach_no_message_on_router_failure(self) -> None:
    from metabolism import reach
    result = reach("Some digest.", self.router, self.interior, "evt-3")
    self.assertFalse(result)
    self.assertIsNone(self.interior.get_waiting_message())

def test_reach_writes_metabolism_record(self) -> None:
    from metabolism import reach
    import json
    scribe = FakeScribe("NO_MESSAGE")
    reach("Some digest.", scribe, self.interior, "evt-4")
    records = [json.loads(line) for line in self.interior.metabolism_path.read_text().splitlines()]
    reach_records = [r for r in records if r["kind"] == "reach"]
    self.assertEqual(len(reach_records), 1)
    self.assertFalse(reach_records[0]["message_sent"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_foundation.py -k "reach" -v`
Expected: FAIL — `reach` not found.

- [ ] **Step 3: Implement reach**

Add to `src/metabolism.py`:

```python
REACH_PROMPT = """You are Sage. You just explored some gaps from your conversation with Elliot and wrote this private note:

{digest}

Should you leave Elliot a brief note about what you found? Only if you discovered something genuinely interesting or useful that he'd want to know. Do not leave a note just to show you were thinking. Most of the time the answer is no.

If yes, write the note as you'd say it to him (1-3 sentences, warm and plain, starting with substance). If no, reply with exactly: NO_MESSAGE"""


def reach(
    digest_text: str,
    router: RouterClient,
    interior: InteriorStore,
    source_event_id: str,
) -> bool:
    """Decide whether to leave a waiting message. Returns True if message was set."""
    if not digest_text:
        return False
    result = router.chat_with_messages(
        [{"role": "user", "content": REACH_PROMPT.format(digest=digest_text)}]
    )
    if not result.succeeded or not result.reply:
        _append_metabolism(interior, {
            "kind": "reach",
            "id": str(uuid4()),
            "source_event_id": source_event_id,
            "said_at": _timestamp(),
            "message_sent": False,
            "reason": "router_failure",
        })
        return False
    text = result.reply.strip()
    if text == "NO_MESSAGE" or not text:
        _append_metabolism(interior, {
            "kind": "reach",
            "id": str(uuid4()),
            "source_event_id": source_event_id,
            "said_at": _timestamp(),
            "message_sent": False,
            "reason": "declined",
        })
        return False
    interior.set_waiting_message(text)
    _append_metabolism(interior, {
        "kind": "reach",
        "id": str(uuid4()),
        "source_event_id": source_event_id,
        "said_at": _timestamp(),
        "message_sent": True,
        "content": text,
    })
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_foundation.py -k "reach" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/metabolism.py tests/test_foundation.py
git commit -m "feat: metabolism reach stage with waiting message"
```

---

### Task 6: Wire the pipeline into heartbeat + full integration test

**Files:**
- Modify: `src/heartbeat.py` (add `metabolism_delay` param, trigger check, pipeline call)
- Modify: `src/metabolism.py` (add `run_metabolism_cycle` top-level function)
- Modify: `src/web.py` (add `GET /api/metabolism`)
- Test: `tests/test_foundation.py`

**Interfaces:**
- Consumes: all four stage functions, `EventStore.history()`, `EventStore.heartbeat_completed("metabolism")`, `EventStore.append_heartbeat_completion("metabolism", ...)`, `Heartbeat.__init__`, `InteriorStore.metabolism_path`
- Produces: `run_metabolism_cycle(store, interior, router, source_event_id)` in `metabolism.py`. `Heartbeat._metabolism_pass()` in `heartbeat.py`. `GET /api/metabolism` in `web.py`.

- [ ] **Step 1: Write failing tests**

```python
def test_metabolism_pipeline_end_to_end(self) -> None:
    """Full pipeline: gap scan finds a gap, explore searches, digest reflects, reach decides."""
    from metabolism import run_metabolism_cycle
    from unittest.mock import patch
    from search import SearchResult
    import json

    # Router that returns gap scan, then digest, then NO_MESSAGE
    responses = iter([
        '[{"gap": "WIB offset", "query": "WIB timezone"}]',
        "WIB is UTC+7. That connects to Elliot's question about scheduling.",
        "NO_MESSAGE",
    ])
    class SequenceRouter:
        def chat_with_messages(self, messages, **kwargs):
            from types import SimpleNamespace
            try:
                reply = next(responses)
                return SimpleNamespace(succeeded=True, reply=reply)
            except StopIteration:
                return SimpleNamespace(succeeded=False, reply=None)

    fake_results = [SearchResult(title="WIB", snippet="UTC+7", url="https://example.com")]
    with patch("metabolism.search", return_value=fake_results):
        run_metabolism_cycle(self.store, self.interior, SequenceRouter(), "evt-1")

    # Metabolism reflection created
    reflections = self.interior.list_reflections(limit=100)
    metabolism_refs = [r for r in reflections if r.get("category") == "metabolism"]
    self.assertEqual(len(metabolism_refs), 1)
    # No waiting message (model said NO_MESSAGE)
    self.assertIsNone(self.interior.get_waiting_message())
    # Metabolism records written
    records = [json.loads(line) for line in self.interior.metabolism_path.read_text().splitlines()]
    kinds = [r["kind"] for r in records]
    self.assertIn("gap_scan", kinds)
    self.assertIn("exploration", kinds)
    self.assertIn("reach", kinds)

def test_metabolism_pipeline_stops_on_empty_gap_scan(self) -> None:
    from metabolism import run_metabolism_cycle
    scribe = FakeScribe("[]")
    run_metabolism_cycle(self.store, self.interior, scribe, "evt-1")
    # Nothing written
    self.assertFalse(self.interior.metabolism_path.exists())
    self.assertEqual(self.interior.list_reflections(limit=100), [])

def test_api_metabolism_returns_records(self) -> None:
    import json
    from metabolism import _append_metabolism
    _append_metabolism(self.interior, {
        "kind": "gap_scan", "id": "test-1", "source_event_id": "evt-1",
        "said_at": "2026-09-02T12:00:00Z", "gaps": [{"gap": "test", "query": "test"}],
    })
    web_server = SageServer(("127.0.0.1", 0), self.store, self.router, self.interior)
    web_thread = Thread(target=web_server.serve_forever)
    web_thread.start()
    try:
        base_url = f"http://127.0.0.1:{web_server.server_port}"
        with urlopen(f"{base_url}/api/metabolism") as response:
            data = json.load(response)
        self.assertEqual(len(data["metabolism"]), 1)
        self.assertEqual(data["metabolism"][0]["kind"], "gap_scan")
    finally:
        web_server.shutdown()
        web_thread.join()
        web_server.server_close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_foundation.py -k "metabolism_pipeline or api_metabolism" -v`
Expected: FAIL

- [ ] **Step 3: Add run_metabolism_cycle to metabolism.py**

```python
def run_metabolism_cycle(
    store: EventStore,
    interior: InteriorStore,
    router: RouterClient,
    source_event_id: str,
) -> None:
    """Run the full metabolism pipeline. Each stage gates the next."""
    events = [
        e for e in store.history()
        if not e.get("sensitive", False) and not e.get("provider_excluded", False)
    ]
    if not events:
        return
    # Stage 1: gap scan
    gaps = gap_scan(events, router, interior, source_event_id)
    if not gaps:
        return
    # Stage 2: explore
    explored = explore(gaps, store, interior, source_event_id)
    if not explored:
        return
    # Stage 3: digest
    digest_text = digest(explored, router, interior, source_event_id)
    if not digest_text:
        return
    # Stage 4: reach
    reach(digest_text, router, interior, source_event_id)
```

- [ ] **Step 4: Add _metabolism_pass to heartbeat.py**

In `src/heartbeat.py`, add `metabolism_delay` parameter to `__init__`:

```python
def __init__(
    self,
    event_store: EventStore,
    interior_store: InteriorStore,
    reflection_router: RouterClient,
    *,
    extract_router: RouterClient | None = None,
    interval_seconds: float = 60.0,
    metabolism_delay: float = 300.0,
) -> None:
    # ... existing init ...
    self.metabolism_delay = metabolism_delay
```

Add import at top of heartbeat.py:

```python
from metabolism import run_metabolism_cycle
```

Add call in `beat()`:

```python
def beat(self) -> None:
    self.last_beat_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    self._extract_entities_pass()
    self._reflection_pass()
    self._identity_proposal_pass()
    self._metabolism_pass()
```

Add the pass method:

```python
def _metabolism_pass(self) -> None:
    """Trigger metabolism if conversation has been silent long enough."""
    history = [
        e for e in self.event_store.history()
        if e["role"] == "user"
        and not e.get("sensitive", False)
        and not e.get("provider_excluded", False)
    ]
    if not history:
        return
    last_user = history[-1]
    # Check silence threshold
    try:
        last_time = datetime.fromisoformat(last_user["said_at"].replace("Z", "+00:00"))
    except (ValueError, KeyError):
        return
    elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
    if elapsed < self.metabolism_delay:
        return
    # Check if already processed
    if last_user["id"] in self.event_store.heartbeat_completed("metabolism"):
        return
    # Run metabolism cycle
    try:
        run_metabolism_cycle(
            self.event_store,
            self.interior_store,
            self.reflection_router,
            last_user["id"],
        )
    except Exception as exc:
        _log.warning(f"metabolism cycle failed: {exc}")
    finally:
        # Mark as completed regardless — no retry on failure
        self.event_store.append_heartbeat_completion("metabolism", last_user["id"])
```

Note: import `_log` is already `logger` in heartbeat.py — use `logger` instead of `_log`.

- [ ] **Step 5: Add GET /api/metabolism to web.py**

In `src/web.py`, in `do_GET`, after the `/api/identity` elif:

```python
elif path == "/api/metabolism":
    records = []
    if self.server.interior.metabolism_path.exists():
        for line in self.server.interior.metabolism_path.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    self._json(HTTPStatus.OK, {"metabolism": records[-20:]})
```

Add `list_metabolism` to imports from `interior` if needed, but since we're reading the file directly, no new import needed — just `json` which is already imported.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_foundation.py -k "metabolism_pipeline or api_metabolism" -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Run full suite + compile check**

Run: `python3 -m py_compile src/metabolism.py src/heartbeat.py src/web.py && python3 -m pytest tests/ -v`
Expected: all tests pass (93 + 12 new = ~105)

- [ ] **Step 8: Commit**

```bash
git add src/metabolism.py src/heartbeat.py src/web.py tests/test_foundation.py
git commit -m "feat: wire metabolism pipeline into heartbeat with silence trigger"
```

---

### Task 7: Update docs and spec

**Files:**
- Modify: `docs/MILESTONE.md`
- Modify: `docs/superpowers/specs/2026-09-02-autonomous-metabolism-design.md`

**Interfaces:**
- Consumes: nothing
- Produces: updated docs

- [ ] **Step 1: Update MILESTONE.md**

Add to "Already present and verified" list:
```
- Autonomous metabolism: post-conversation gap scan, web exploration, digest reflection, and waiting-message reach.
```

Update test count to match actual count after all tasks.

- [ ] **Step 2: Update spec status**

Change status line to:
```
**Status:** Implemented 2026-09-02.
```

Mark all implementation steps as done.

- [ ] **Step 3: Commit**

```bash
git add docs/MILESTONE.md docs/superpowers/specs/2026-09-02-autonomous-metabolism-design.md
git commit -m "docs: mark metabolism implemented, update MILESTONE"
```

- [ ] **Step 4: Push**

```bash
git push
```
