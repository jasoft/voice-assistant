"""Fast-chat harness chain tests for try_fast_memory_chat.

Mocks TypeSafe and the DeepSeek Harness chat-fast calls; exercises the
拆词 → 一次 CEL → 回答 chain, the record branch, and the TypeSafe-failure
fallback (None).
"""

from unittest.mock import patch

import pytest

from press_to_talk.api import fast_chat


class _FakeMemos:
    """Minimal MemosClient stand-in used by _build_memos_client mock."""

    def __init__(self, items: list[dict]):
        self.items = items

    def create_memo(self, content: str, visibility: str = "PRIVATE") -> dict:
        return {"name": "memos/new"}

    def list_memos(self, *, page_size: int = 10, filter_expr: str = "", timeout=None, **kwargs) -> dict:
        return {"memos": self.items, "nextPageToken": ""}


@pytest.mark.anyio
async def test_record_branch_writes_memo():
    fake = _FakeMemos([])
    with (
        patch.object(fast_chat, "ask_is_record", return_value="record"),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
    ):
        result = await fast_chat.try_fast_memory_chat("帮我记一下护照在书房")
    assert result is not None
    assert result["debug_info"]["intent"] == "record"
    assert "已记入" in result["reply"]


@pytest.mark.anyio
async def test_typesafe_none_returns_none():
    fake = _FakeMemos([])
    with (
        patch.object(fast_chat, "ask_is_record", return_value=None),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
    ):
        result = await fast_chat.try_fast_memory_chat("你好呀")
    assert result is None


@pytest.mark.anyio
async def test_ask_chain_uses_harness_and_cel():
    fake = _FakeMemos([
        {"name": "memos/1", "content": "护照放在书房白柜子", "createTime": "2026-09-20T10:00:00Z"},
    ])
    captured: dict = {}

    async def fake_extract(query: str) -> list[str]:
        return ["护照", "书房"]

    async def fake_answer(query: str, memos: list[dict]) -> str:
        captured["memos"] = memos
        return "护照放在书房白色柜子的第一个抽屉里。"

    with (
        patch.object(fast_chat, "ask_is_record", return_value="other"),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(fast_chat, "_extract_keywords_with_harness", new=fake_extract),
        patch.object(fast_chat, "_answer_with_harness", new=fake_answer),
    ):
        result = await fast_chat.try_fast_memory_chat("我的护照在哪里")

    assert result is not None
    assert result["debug_info"]["intent"] == "ask"
    assert result["debug_info"]["keywords"] == ["护照", "书房"]
    assert result["debug_info"]["memo_count"] == 1
    assert "白色柜子" in result["reply"]
    assert captured["memos"][0]["memory"] == "护照放在书房白柜子"


@pytest.mark.anyio
async def test_no_context_still_answers_with_chit_chat():
    fake = _FakeMemos([])
    captured: dict = {}

    async def fake_extract(query: str) -> list[str]:
        return ["天气"]

    async def fake_answer(query: str, memos: list[dict]) -> str:
        captured["memos"] = memos
        return "今天上海多云，气温 24℃。"

    with (
        patch.object(fast_chat, "ask_is_record", return_value="other"),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(fast_chat, "_extract_keywords_with_harness", new=fake_extract),
        patch.object(fast_chat, "_answer_with_harness", new=fake_answer),
    ):
        result = await fast_chat.try_fast_memory_chat("上海今天天气怎么样")

    assert result is not None
    assert result["debug_info"]["memo_count"] == 0
    assert "多云" in result["reply"]
    assert captured["memos"] == []  # 无上下文时仍正常闲聊回答


def test_parse_keywords_json_formats():
    assert fast_chat._parse_keywords_json('{"keywords":["护照","书房"]}') == ["护照", "书房"]
    assert fast_chat._parse_keywords_json('["护照", "书房"]') == ["护照", "书房"]
    assert fast_chat._parse_keywords_json('```json\n{"keywords":["护照"]}\n```') == ["护照"]
    assert fast_chat._parse_keywords_json("没有关键词") == []
