"""
launch.py — Sage: Local AI Companion
=====================================
Entry point. Keeps only:
  - HTTP client lifecycle
  - Job store (async polling)
  - Quart routes
  - Startup sequence

All cognition, memory, and model logic is in submodules.

Runs on: http://localhost:6969
"""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from quart import Quart, jsonify, request

import sys
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    DAEMON_EMOTION_TRIGGER,
    DAEMON_TURN_TRIGGER,
    DIRECTIVE_FILE,
    HISTORY_FILE,
    HISTORY_TURNS,
    LIBRARY_CATS,
    LIBRARY_DIR,
    PORT,
)
from daemon.reflection_daemon import ReflectionDaemon
from memory.retrieval import retrieve_relevant_memories
from memory.storage import (
    append_history,
    history_for_prompt,
    load_history,
    read_text,
    safe_stem,
    strip_last_assistant,
    write_text,
)
from models.inference import chat_stream
from models.prompts import build_chat_messages
from utils.bootstrap import distill_legacy_history, ensure_filesystem
from utils.logger import log

# ── Globals ──────────────────────────────────────────────────────────

_client: httpx.AsyncClient = None
_daemon: ReflectionDaemon  = None

# ── Job store ────────────────────────────────────────────────────────
# Each chat request spawns a background job.
# The frontend polls /api/poll/<jid> for tokens.

_jobs: dict[str, dict] = {}
_jlock = asyncio.Lock()


async def _jcreate(jid: str) -> None:
    async with _jlock:
        _jobs[jid] = {"chunks": [], "done": False, "error": None, "status": "thinking"}


async def _jappend(jid: str, tok: str) -> None:
    async with _jlock:
        if jid in _jobs:
            _jobs[jid]["chunks"].append(tok)


async def _jset_status(jid: str, status: str) -> None:
    async with _jlock:
        if jid in _jobs:
            _jobs[jid]["status"] = status


async def _jfinish(jid: str, err: str = None) -> None:
    async with _jlock:
        if jid in _jobs:
            _jobs[jid].update({"done": True, "error": err, "status": "done"})


async def _jread(jid: str, frm: int = 0) -> dict:
    async with _jlock:
        j = _jobs.get(jid)
        if not j:
            return {"found": False}
        chunks = j["chunks"][frm:]
        return {
            "found": True,
            "text": "".join(chunks),
            "new_count": len(chunks),
            "total": len(j["chunks"]),
            "done": j["done"],
            "error": j["error"],
            "status": j.get("status", "thinking"),
        }


# ── Session state ────────────────────────────────────────────────────

_session_turns = 0          # assistant turns this session
_recent_digest: list[str] = []   # last N turns for daemon trigger


def _update_session(role: str, content: str) -> None:
    """Track session turns and build digest for daemon."""
    global _session_turns
    _recent_digest.append(f"{role.upper()}: {content}")
    if len(_recent_digest) > 20:
        _recent_digest.pop(0)
    if role == "assistant":
        _session_turns += 1


def _should_trigger_daemon() -> bool:
    """
    Trigger daemon if:
      - enough turns have accumulated, OR
      - emotional language detected in recent turns
    """
    if _session_turns > 0 and _session_turns % DAEMON_TURN_TRIGGER == 0:
        return True

    # Simple emotional signal heuristic
    emotional_keywords = {
        "hate", "love", "scared", "afraid", "angry", "sad", "happy",
        "miss", "lonely", "tired", "exhausted", "excited", "hurt",
        "benci", "takut", "marah", "sedih", "senang", "kangen", "lelah",
    }
    recent_text = " ".join(_recent_digest[-DAEMON_EMOTION_TRIGGER * 2:]).lower()
    if any(kw in recent_text for kw in emotional_keywords):
        return True

    return False


# ── Background job runner ────────────────────────────────────────────

async def _run_job(jid: str, messages: list[dict], user_input: str) -> None:
    """Stream response, persist history, optionally trigger daemon."""
    try:
        parts = []
        first = True

        async for token in chat_stream(messages, _client):
            if first:
                await _jset_status(jid, "streaming")
                first = False
            await _jappend(jid, token)
            parts.append(token)

        full_response = "".join(parts)
        await append_history(HISTORY_FILE, "assistant", full_response)
        _update_session("assistant", full_response)

        # Trigger reflection daemon if warranted
        if _daemon and _should_trigger_daemon():
            digest = "\n".join(_recent_digest)
            log("daemon", "triggered", session_turns=_session_turns)
            _daemon.trigger(digest)

        await _jfinish(jid)

    except Exception as e:
        log("inference", "job_error", jid=jid, error=str(e))
        await _jfinish(jid, err=str(e))


# ── Quart app ────────────────────────────────────────────────────────

app = Quart(__name__)


@app.before_serving
async def startup() -> None:
    global _client, _daemon

    # Filesystem
    ensure_filesystem()

    # Shared HTTP client
    _client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=8),
        timeout=360.0,
    )

    # Daemon
    _daemon = ReflectionDaemon(_client)

    # First-boot: distill legacy history
    asyncio.create_task(distill_legacy_history(_client))

    print("─" * 48)
    print("  Sage  —  local AI companion (llama.cpp)")
    print("─" * 48)
    print(f"  UI       →  http://localhost:{PORT}")
    print(f"  chat     →  Port 8080")
    print(f"  memory   →  Port 8081")
    print(f"  embeds   →  Port 8082")
    print(f"  data     →  {DIRECTIVE_FILE.parent}")
    print("─" * 48)


@app.after_serving
async def shutdown() -> None:
    if _client:
        await _client.aclose()


# ── Routes ───────────────────────────────────────────────────────────

@app.route("/")
async def index():
    html_path = Path(__file__).parent / "frontend" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>Sage</h1><p>frontend/index.html not found.</p>", 404


@app.route("/api/history")
async def api_history():
    history = await load_history(HISTORY_FILE)
    return jsonify({"messages": history})


@app.route("/api/file")
async def api_file():
    name = request.args.get("name", "")
    if name == "directive":
        content = await read_text(DIRECTIVE_FILE)
        return jsonify({"content": content})
    return jsonify({"error": "unknown file"}), 400


@app.route("/api/file/directive", methods=["POST"])
async def api_save_directive():
    data = await request.get_json(force=True)
    await write_text(DIRECTIVE_FILE, data.get("content", ""))
    return jsonify({"ok": True})


@app.route("/api/library")
async def api_library():
    tree = {}
    for cat in LIBRARY_CATS:
        files = sorted((LIBRARY_DIR / cat).glob("*.txt"))
        tree[cat] = [f.stem for f in files]
    return jsonify(tree)


@app.route("/api/library/file")
async def api_library_file():
    cat  = request.args.get("cat", "")
    name = request.args.get("name", "")
    if cat not in LIBRARY_CATS or not name:
        return jsonify({"error": "invalid"}), 400
    path = LIBRARY_DIR / cat / f"{safe_stem(name)}.txt"
    if not path.exists():
        return jsonify({"error": "not found"}), 404
    content = await read_text(path)
    return jsonify({"content": content})


@app.route("/api/library/file", methods=["POST"])
async def api_save_library_file():
    data = await request.get_json(force=True)
    cat, name, content = data.get("cat"), data.get("name"), data.get("content", "")
    if cat not in LIBRARY_CATS or not name:
        return jsonify({"error": "invalid"}), 400
    path = LIBRARY_DIR / cat / f"{safe_stem(name)}.txt"
    await write_text(path, content)
    return jsonify({"ok": True})


@app.route("/api/library/file", methods=["DELETE"])
async def api_delete_library_file():
    cat  = request.args.get("cat", "")
    name = request.args.get("name", "")
    if cat not in LIBRARY_CATS or not name:
        return jsonify({"error": "invalid"}), 400
    path = LIBRARY_DIR / cat / f"{safe_stem(name)}.txt"
    if path.exists():
        path.unlink()
    return jsonify({"ok": True})


@app.route("/api/library/file/new", methods=["POST"])
async def api_new_library_file():
    data = await request.get_json(force=True)
    cat, name = data.get("cat"), (data.get("name") or "").strip()
    if cat not in LIBRARY_CATS or not name:
        return jsonify({"error": "invalid"}), 400
    stem = safe_stem(name)
    if not stem:
        return jsonify({"error": "bad name"}), 400
    path = LIBRARY_DIR / cat / f"{stem}.txt"
    if path.exists():
        return jsonify({"error": "exists"}), 409
    await write_text(path, "")
    return jsonify({"ok": True, "safe_name": stem})


@app.route("/api/chat", methods=["POST"])
async def api_chat():
    data = await request.get_json(force=True)
    user_input = (data.get("message") or "").strip()
    if not user_input:
        return jsonify({"error": "empty"}), 400

    # Persist user turn
    await append_history(HISTORY_FILE, "user", user_input)
    _update_session("user", user_input)

    # Build prompt
    history    = await load_history(HISTORY_FILE)
    directive  = await read_text(DIRECTIVE_FILE)
    memory     = await retrieve_relevant_memories(user_input, _client)
    prompt_history = history_for_prompt(history[:-1], HISTORY_TURNS)
    messages   = build_chat_messages(directive, user_input, prompt_history, memory)

    # Spawn background job
    jid = str(uuid.uuid4())
    await _jcreate(jid)
    asyncio.create_task(_run_job(jid, messages, user_input))

    return jsonify({"job_id": jid})


@app.route("/api/poll/<jid>")
async def api_poll(jid: str):
    frm = int(request.args.get("from", 0))
    result = await _jread(jid, frm)
    if not result["found"]:
        return jsonify({"error": "not found"}), 404
    return jsonify(result)


@app.route("/api/retry", methods=["POST"])
async def api_retry():
    history = await load_history(HISTORY_FILE)
    last_user = next(
        (m["content"] for m in reversed(history) if m["role"] == "user"), None
    )
    if not last_user:
        return jsonify({"error": "no user message"}), 400

    # Remove the previous assistant turn from JSONL before the new one is written.
    # Without this, _run_job appends a second assistant entry and the history
    # ends up with two consecutive assistant turns.
    await strip_last_assistant(HISTORY_FILE)

    directive = await read_text(DIRECTIVE_FILE)
    # Strip last assistant turn from prompt context if present
    ctx = history[:-1] if history and history[-1]["role"] == "assistant" else history
    memory = await retrieve_relevant_memories(last_user, _client)
    prompt_history = history_for_prompt(ctx, HISTORY_TURNS)
    messages = build_chat_messages(directive, last_user, prompt_history, memory)

    jid = str(uuid.uuid4())
    await _jcreate(jid)
    asyncio.create_task(_run_job(jid, messages, last_user))
    return jsonify({"job_id": jid})


@app.route("/api/status")
async def api_status():
    """Quick health check — shows session state."""
    return jsonify({
        "session_turns": _session_turns,
        "daemon_running": _daemon._running if _daemon else False,
        "daemon_last_run": _daemon._last_run if _daemon else 0,
    })


# ── Entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
