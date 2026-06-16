"""Verify the active-chat idle boundary fix.

Tests:
1. session.begin_chat() makes chat_active() true.
2. session.end_chat() makes chat_active() false.
3. Counter does not go negative if end_chat() is called too many times.
4. _maybe_reflect() returns without calling run_reflection while chat is active.

Run: source .venv/bin/activate && python test_boundary.py
"""

import sys
import time
from unittest.mock import AsyncMock, patch

# ── Test 1: begin_chat sets chat_active ──────────────────────────────
from backend.session import session

assert not session.chat_active(), "chat_active should be False at startup"

session.begin_chat()
assert session.chat_active(), "chat_active should be True after begin_chat"

# ── Test 2: end_chat clears chat_active ──────────────────────────────
session.end_chat()
assert not session.chat_active(), "chat_active should be False after end_chat"

session.begin_chat()
session.begin_chat()  # two concurrent
assert session.chat_active(), "chat_active should be True with 2 active"
session.end_chat()
assert session.chat_active(), "chat_active should still be True with 1 active"
session.end_chat()
assert not session.chat_active(), "chat_active should be False after both ended"

print("PASS: begin_chat/end_chat toggle correctly (basic + nested)")

# ── Test 3: end_chat does not go negative ──────────────────────────────
session.end_chat()
session.end_chat()
assert session._active_chats == 0, "active_chats should clamp at 0"
assert not session.chat_active(), "chat_active should be False after excess end_chat"

print("PASS: extra end_chat calls do not corrupt counter")

# ── Test 4: heartbeat gate while chat is active ──────────────────────
from backend.heartbeat import Heartbeat

session.begin_chat()  # simulate active chat

hb = Heartbeat(AsyncMock())

# Mock out the things _maybe_reflect would call if gates passed
hb._maybe_search = AsyncMock()

with patch.object(hb, '_lock') as mock_lock:
    mock_lock.locked.return_value = False  # not already reflecting
    hb._reflecting = False
    hb._last_reflection_ts = 0.0
    hb._last_search_ts = 0.0

    # Patch run_reflection at module level so we can detect it being called
    with patch('backend.heartbeat.run_reflection', new=AsyncMock()) as mock_run:
        import asyncio
        asyncio.run(hb._maybe_reflect())

        mock_run.assert_not_awaited()
        print("PASS: _maybe_reflect did NOT call run_reflection while chat active")

session.end_chat()

# ── Also verify that without active chat, the gate does not block ──
# (just confirm it reaches past the gate; it'll still hit idle gate)
async def _test_heartbeat_passes_gate():
    with patch.object(hb, '_lock') as mock_lock:
        mock_lock.locked.return_value = False
        with patch('backend.heartbeat.session.idle_seconds', return_value=0.0) as mock_idle:
            await hb._maybe_reflect()
            # It should get past the chat_active gate, then hit the idle gate
            mock_idle.assert_called_once()

asyncio.run(_test_heartbeat_passes_gate())
print("PASS: heartbeat gate allows reflection when no chat is active")

print(f"\nAll 4 tests passed ({__file__})")
