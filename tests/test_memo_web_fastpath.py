"""Memo Web fast-path integration tests.

/api/query must serve record/find instructions via try_fast_memory_chat
(fast-chat agent) and only fall back to the Harness Agent otherwise.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from press_to_talk.memo_web import app


async def _post(instruction: str) -> tuple[int, dict]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/query", json={"instruction": instruction})
    return resp.status_code, resp.json()


@pytest.mark.anyio
async def test_record_query_served_by_fast_path():
    fast_result = {
        "reply": "✅ 已记入 Memos：测试记录",
        "memories": [],
        "query": "帮我记一下测试",
        "debug_info": {"backend": "fast-chat", "intent": "record"},
    }
    with patch(
        "press_to_talk.api.fast_chat.try_fast_memory_chat",
        new=AsyncMock(return_value=fast_result),
    ):
        status, body = await _post("帮我记一下测试")
    assert status == 200
    assert body["agent"] == "fast-chat"
    assert "已记入" in body["reply"]


@pytest.mark.anyio
async def test_find_query_served_by_fast_path():
    fast_result = {
        "reply": "大王，护照存放在书房白色柜子的第一个抽屉里。",
        "memories": [],
        "query": "我的护照在哪里",
        "debug_info": {"backend": "fast-chat", "intent": "find"},
    }
    with patch(
        "press_to_talk.api.fast_chat.try_fast_memory_chat",
        new=AsyncMock(return_value=fast_result),
    ):
        status, body = await _post("我的护照在哪里")
    assert status == 200
    assert body["agent"] == "fast-chat"
    assert "护照" in body["reply"]


@pytest.mark.anyio
async def test_non_memory_query_falls_back_to_harness():
    harness_result = {
        "reply": "你好呀！有什么可以帮你的吗？",
        "debug_info": {"session_id": "sess-1"},
    }
    mock_harness = AsyncMock()
    mock_harness.query.return_value = harness_result
    app.state.harness_client = mock_harness
    with patch(
        "press_to_talk.api.fast_chat.try_fast_memory_chat",
        new=AsyncMock(return_value=None),
    ):
        status, body = await _post("你好呀")
    assert status == 200
    assert body["reply"] == "你好呀！有什么可以帮你的吗？"
    assert body["session_id"] == "sess-1"
    mock_harness.query.assert_awaited_once_with("你好呀")


@pytest.mark.anyio
async def test_fast_path_exception_falls_back_to_harness():
    harness_result = {
        "reply": "harness reply",
        "debug_info": {"session_id": "sess-2"},
    }
    mock_harness = AsyncMock()
    mock_harness.query.return_value = harness_result
    app.state.harness_client = mock_harness
    with patch(
        "press_to_talk.api.fast_chat.try_fast_memory_chat",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        status, body = await _post("随便聊聊")
    assert status == 200
    assert body["reply"] == "harness reply"
