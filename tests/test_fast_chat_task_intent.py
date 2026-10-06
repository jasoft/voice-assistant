"""fast_chat task 意图分流测试：intent == "task" 进入项目任务入口并回退。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from press_to_talk.api import fast_chat, project_entry

from test_fast_chat_harness import _FakeMemos, isolate_harness_tests_from_live_completions  # noqa: F401


@pytest.mark.anyio
async def test_task_intent_dispatches_to_project_entry():
    entry_result = {
        "reply": "好的，已把任务转交给Codex处理「voice-assistant」项目。",
        "action": "speak",
        "memories": [],
        "query": "让Codex给语音助手加上任务进度查询",
        "debug_info": {"backend": "fast-chat", "intent": "task", "task_action": "new"},
    }
    fake = _FakeMemos([])
    with (
        patch.object(
            fast_chat,
            "ask_intent_and_delivery",
            return_value={"intent": "task", "delivery": "speak"},
        ),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(
            project_entry,
            "handle_project_task_intent",
            new=AsyncMock(return_value=entry_result),
        ) as handler,
    ):
        result = await fast_chat.try_fast_memory_chat("让Codex给语音助手加上任务进度查询")
    handler.assert_awaited_once()
    assert result is not None
    assert result["debug_info"]["intent"] == "task"
    assert "elapsed_task_s" in result["debug_info"]


@pytest.mark.anyio
async def test_task_intent_falls_back_to_harness_when_entry_returns_none():
    fake = _FakeMemos([])
    with (
        patch.object(
            fast_chat,
            "ask_intent_and_delivery",
            return_value={"intent": "task", "delivery": "speak"},
        ),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(
            project_entry,
            "handle_project_task_intent",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await fast_chat.try_fast_memory_chat("把刚才那个任务停下来")
    assert result is None  # 回退 Harness 慢路径
