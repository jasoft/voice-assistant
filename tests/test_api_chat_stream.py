"""Streaming must deliver before completion and cancel cleanly without reruns."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from press_to_talk.api import main, fast_chat
from press_to_talk.api.reply_stream import ReplyStream, current_stream, complete_reply


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_first_delta_arrives_before_completion_and_cancel_stops_producer(monkeypatch):
    release, cancelled = asyncio.Event(), asyncio.Event()
    async def handle(req, user):
        try:
            await current_stream.get().emit("第一句。")
            await release.wait()
            return main.QueryResponse(reply="第一句。", query=req.query)
        finally:
            cancelled.set()
    monkeypatch.setattr(main, "_handle_chat", handle)
    response = main._stream_chat(main.QueryRequest(query="你好", stream=True), "soj")
    iterator = response.body_iterator
    assert "connected" in await anext(iterator)
    first = await asyncio.wait_for(anext(iterator), 1)
    assert '"text": "第一句。"' in first
    assert not release.is_set()
    await iterator.aclose()
    assert cancelled.is_set()


@pytest.mark.anyio
async def test_done_includes_metadata_and_atomic_result_is_not_fake_tokenized(monkeypatch):
    async def handle(req, user):
        return main.QueryResponse(reply="已记录完整结果", query=req.query, action="speak", debug_info={"intent": "record"})
    monkeypatch.setattr(main, "_handle_chat", handle)
    events = [event async for event in main._stream_chat(main.QueryRequest(query="记录", stream=True), "soj").body_iterator]
    assert sum("event: delta" in e for e in events) == 1
    assert '"intent": "record"' in events[-1]
    assert "event: done" in events[-1]


@pytest.mark.anyio
async def test_partial_failure_never_reports_done_or_falls_back(monkeypatch):
    async def fast(*args, **kwargs):
        await current_stream.get().emit("部分答案")
        raise RuntimeError("upstream broken")
    monkeypatch.setattr(main, "base_config", SimpleNamespace())
    monkeypatch.setattr(main, "try_fast_memory_chat", fast)
    fallback = AsyncMock()
    monkeypatch.setattr(main, "_execute_harness_query", fallback)
    events = [e async for e in main._stream_chat(main.QueryRequest(query="你好", stream=True), "soj").body_iterator]
    assert "部分答案" in events[1]
    assert "event: error" in events[-1]
    assert all("event: done" not in e for e in events)
    fallback.assert_not_called()


@pytest.mark.anyio
async def test_upstream_tokens_are_forwarded_and_reasoning_is_private():
    emitted = []
    class Response:
        closed = False
        async def __aiter__(self):
            for content, reasoning, finish in [(None, "private", None), ("你好", None, None), ("。", None, "stop")]:
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content, reasoning_content=reasoning), finish_reason=finish)])
        async def close(self): self.closed = True
    response = Response()
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    async def send(text): emitted.append(text)
    token = current_stream.set(ReplyStream(send, watch=True))
    try:
        reply = await complete_reply(client, model="fast", messages=[])
    finally:
        current_stream.reset(token)
    assert emitted == ["你好", "。"]
    assert reply == "你好。"
    assert reply.reasoning == "private"
    assert create.call_args.kwargs["stream"] is True
    assert "Apple Watch" in create.call_args.kwargs["messages"][-1]["content"]
    assert response.closed


def test_json_default_sse_opt_in_and_auth(monkeypatch):
    monkeypatch.setenv("PTT_QUERY_BACKEND", "harness")
    monkeypatch.setenv("PTT_API_KEY", "test-token")
    async def handle(req, user): return main.QueryResponse(reply="你好", query=req.query)
    monkeypatch.setattr(main, "_handle_chat", handle)
    client = TestClient(main.app)
    assert client.post("/v1/chat", json={"query": "你好", "stream": True}).status_code in (401, 403)
    client.headers["Authorization"] = "Bearer test-token"
    response = client.post("/v1/chat", json={"query": "你好"})
    assert response.json()["reply"] == "你好"
    for path in ("/v1/chat", "/chat"):
        response = client.post(path, json={"query": "你好", "stream": True, "response_style": "watch"})
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert "event: done" in response.text
    assert client.post("/v1/chat", json={"query": "你好", "response_style": "bad"}).status_code == 422


@pytest.mark.anyio
async def test_upstream_eof_without_stop_is_failure_and_closes_stream():
    class Response:
        closed = False
        async def __aiter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="未完", reasoning_content=None), finish_reason=None)])
        async def close(self): self.closed = True
    response = Response()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=response))))
    emitted = []
    async def send(text): emitted.append(text)
    token = current_stream.set(ReplyStream(send))
    try:
        with pytest.raises(RuntimeError, match="连接中断"):
            await complete_reply(client, model="fast", messages=[])
    finally:
        current_stream.reset(token)
    assert emitted == ["未完"]
    assert response.closed
