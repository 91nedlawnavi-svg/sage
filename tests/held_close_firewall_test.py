"""Held-close firewall checks. Runs without providers or live memory."""
from __future__ import annotations

import asyncio

from backend.session import ConversationSession
from models.prompts.templates import build_chat_messages
from memory import semantic_recall


def _consume(response):
    async def run():
        return [chunk async for chunk in response.body_iterator]
    return asyncio.run(run())


def test_history_and_prompt():
    session = ConversationSession()
    session.replace_history([
        {"id": "open-user", "role": "user", "content": "normal history"},
        {"id": "held-user", "role": "user", "content": "HELD_CANARY"},
        {"id": "open-sage", "role": "assistant", "content": "normal reply"},
    ])
    history = session.history({"held-user"})
    assert [turn["content"] for turn in history] == ["normal history", "normal reply"]
    assert session.history(None) == []
    prompt = build_chat_messages("directive", "current question", history)
    rendered = "\n".join(message["content"] for message in prompt)
    assert "HELD_CANARY" not in rendered
    assert "normal history" in rendered


def test_semantic_hard_deny_and_ingress():
    old = (semantic_recall._index, semantic_recall._index_keys,
           semantic_recall._loaded, semantic_recall.RECALL_ENABLED,
           semantic_recall._excluded_keys, semantic_recall._read_jsonl)
    semantic_recall._index = [
        {"key": "held", "kind": "turn", "ts": "2026-01-01T00:00:00",
         "role": "user", "text": "HELD_CANARY", "embedding": [1.0, 0.0]},
        {"key": "open", "kind": "turn", "ts": "2026-01-01T00:00:00",
         "role": "user", "text": "normal recalled detail", "embedding": [0.9, 0.1]},
    ]
    semantic_recall._index_keys = {"held", "open"}
    semantic_recall._loaded = True
    semantic_recall.RECALL_ENABLED = True
    semantic_recall._excluded_keys = lambda: set()
    try:
        block = asyncio.run(semantic_recall.recall(
            "long enough semantic recall question", None,
            query_embedding=[1.0, 0.0], held_close_keys={"held"}))
        assert block is not None and "normal recalled detail" in block
        assert "HELD_CANARY" not in block
        assert asyncio.run(semantic_recall.recall(
            "long enough semantic recall question", None,
            query_embedding=[1.0, 0.0], held_close_keys=None)) is None

        semantic_recall._read_jsonl = lambda path: [{
            "id": "held", "role": "user", "content": "HELD_CANARY", "ts": "now"
        }] if path == semantic_recall.CONVERSATION_PATH else []
        assert semantic_recall._pending_items({"held"}) == []
    finally:
        (semantic_recall._index, semantic_recall._index_keys,
         semantic_recall._loaded, semantic_recall.RECALL_ENABLED,
         semantic_recall._excluded_keys, semantic_recall._read_jsonl) = old


def test_current_held_turn_never_reaches_provider():
    import backend.api.chat as chat
    import cognition.held_close_sense as sensor

    old = {
        "sense": sensor.sense,
        "directive": chat.get_directive,
        "sqlite": chat.MEMORY_CORE_SQLITE,
        "append": chat.append_message,
        "record": chat.intake.record_chat_turn,
        "session": chat.session,
        "search": chat.search,
        "embed": chat.semantic_recall.embed_query,
        "retrieve": None,
        "prompt": chat.build_chat_messages,
        "stream": chat.chat_stream,
    }
    from memory import hybrid_retrieval
    old["retrieve"] = hybrid_retrieval.retrieve
    calls: list[str] = []

    def forbidden(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"held-close invoked {name}")
        return fail

    async def forbidden_async(*args, **kwargs):
        calls.append("async-provider")
        raise AssertionError("held-close invoked async provider")

    async def record(entry, held_close=None):
        calls.append(f"record:{held_close}")

    try:
        sensor.sense = lambda text: True
        chat.get_directive = lambda: "directive"
        chat.MEMORY_CORE_SQLITE = False
        chat.session = ConversationSession()
        chat.append_message = lambda role, content: {
            "id": "held-id", "role": role, "content": content, "ts": "now"}
        chat.intake.record_chat_turn = record
        chat.search = forbidden("search")
        chat.semantic_recall.embed_query = forbidden_async
        hybrid_retrieval.retrieve = forbidden_async
        chat.build_chat_messages = forbidden("prompt")
        chat.chat_stream = forbidden("stream")

        for message in ("confession", "/search confession"):
            response = asyncio.run(chat.chat_endpoint(chat.ChatRequest(message=message)))
            assert _consume(response) == [chat.HELD_CLOSE_ACKNOWLEDGEMENT]
        assert calls == ["record:True", "record:True"]
    finally:
        sensor.sense = old["sense"]
        chat.get_directive = old["directive"]
        chat.MEMORY_CORE_SQLITE = old["sqlite"]
        chat.append_message = old["append"]
        chat.intake.record_chat_turn = old["record"]
        chat.session = old["session"]
        chat.search = old["search"]
        chat.semantic_recall.embed_query = old["embed"]
        hybrid_retrieval.retrieve = old["retrieve"]
        chat.build_chat_messages = old["prompt"]
        chat.chat_stream = old["stream"]


if __name__ == "__main__":
    test_history_and_prompt()
    test_semantic_hard_deny_and_ingress()
    test_current_held_turn_never_reaches_provider()
    print("OK held_close_firewall_test")
