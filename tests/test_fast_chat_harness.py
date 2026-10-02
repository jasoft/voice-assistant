"""Fast-chat harness chain tests for try_fast_memory_chat.

Mocks TypeSafe and the DeepSeek Harness chat-fast calls; exercises the
拆词 → 一次 CEL → 回答 chain, the record branch, the paste (compose) branch,
and the TypeSafe-failure fallback (None).
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
        patch.object(
            fast_chat,
            "ask_intent_and_delivery",
            return_value={"intent": "record", "delivery": None},
        ),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
    ):
        result = await fast_chat.try_fast_memory_chat("帮我记一下护照在书房")
    assert result is not None
    assert result["action"] == "speak"
    assert result["debug_info"]["intent"] == "record"
    assert "已记入" in result["reply"]


@pytest.mark.anyio
async def test_typesafe_none_returns_none():
    fake = _FakeMemos([])
    with (
        patch.object(fast_chat, "ask_intent_and_delivery", return_value=None),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
    ):
        result = await fast_chat.try_fast_memory_chat("你好呀")
    assert result is None


@pytest.mark.anyio
async def test_selection_forwarded_to_typesafe():
    """GUI 传来的选中文本要原样传给 TypeSafe 决策（截断除外）。"""
    fake = _FakeMemos([])
    captured: dict = {}

    def fake_decision(query, selected_text=None):
        captured["query"] = query
        captured["selected_text"] = selected_text
        return {"intent": "other", "delivery": None}

    async def fake_extract(query: str) -> list[str]:
        return []

    async def fake_answer(query: str, memos: list[dict]) -> str:
        return "好的。"

    with (
        patch.object(fast_chat, "ask_intent_and_delivery", new=fake_decision),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(fast_chat, "_extract_keywords_with_harness", new=fake_extract),
        patch.object(fast_chat, "_answer_with_harness", new=fake_answer),
    ):
        await fast_chat.try_fast_memory_chat(
            "改写这句话", selected_text="  原始草稿文本  ",
        )
    assert captured["selected_text"] == "原始草稿文本"


@pytest.mark.anyio
async def test_compose_branch_returns_paste_action():
    """delivery=paste 时走 compose 链路：产出内容 + action=paste。"""
    fake = _FakeMemos([])
    captured: dict = {}

    async def fake_compose(query: str, selection: str) -> str | None:
        captured["query"] = query
        captured["selection"] = selection
        return "这是改写后的正式文本。"

    with (
        patch.object(
            fast_chat,
            "ask_intent_and_delivery",
            return_value={"intent": "other", "delivery": "paste"},
        ),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(fast_chat, "_compose_with_harness", new=fake_compose),
    ):
        result = await fast_chat.try_fast_memory_chat(
            "改写得更正式", selected_text="随手写的草稿",
        )

    assert result is not None
    assert result["action"] == "paste"
    assert result["reply"] == "这是改写后的正式文本。"
    assert result["debug_info"]["intent"] == "compose"
    assert result["debug_info"]["has_selection"] is True
    assert captured["query"] == "改写得更正式"
    assert captured["selection"] == "随手写的草稿"


@pytest.mark.anyio
async def test_compose_branch_without_selection_still_pastes():
    """无选中文本的生成指令（写邮件等）同样走 paste 链路。"""
    fake = _FakeMemos([])

    async def fake_compose(query: str, selection: str) -> str | None:
        assert selection == ""
        return "尊敬的收件人：……"

    with (
        patch.object(
            fast_chat,
            "ask_intent_and_delivery",
            return_value={"intent": "other", "delivery": "paste"},
        ),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(fast_chat, "_compose_with_harness", new=fake_compose),
    ):
        result = await fast_chat.try_fast_memory_chat("帮我写一封会议邀请邮件")

    assert result is not None
    assert result["action"] == "paste"
    assert result["debug_info"]["intent"] == "compose"
    assert result["debug_info"]["has_selection"] is False
    assert "会议邀请" in result["reply"] or len(result["reply"]) > 0


@pytest.mark.anyio
async def test_compose_empty_falls_back_to_speak():
    """compose 失败/产出为空时不粘贴，降级为播报提示。"""
    fake = _FakeMemos([])

    async def fake_compose(query: str, selection: str) -> str | None:
        return None

    with (
        patch.object(
            fast_chat,
            "ask_intent_and_delivery",
            return_value={"intent": "other", "delivery": "paste"},
        ),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(fast_chat, "_compose_with_harness", new=fake_compose),
    ):
        result = await fast_chat.try_fast_memory_chat("改写这句话", selected_text="草稿")

    assert result is not None
    assert result["action"] == "speak"
    assert "失败" in result["reply"]


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
        patch.object(
            fast_chat,
            "ask_intent_and_delivery",
            return_value={"intent": "other", "delivery": None},
        ),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(fast_chat, "_extract_keywords_with_harness", new=fake_extract),
        patch.object(fast_chat, "_answer_with_harness", new=fake_answer),
    ):
        result = await fast_chat.try_fast_memory_chat("我的护照在哪里")

    assert result is not None
    assert result["action"] == "speak"
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
        patch.object(
            fast_chat,
            "ask_intent_and_delivery",
            return_value={"intent": "other", "delivery": None},
        ),
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


@pytest.mark.anyio
async def test_ask_chain_carries_selection_as_context():
    fake = _FakeMemos([])
    captured: dict = {}

    async def fake_extract(query: str, selection: str = "") -> list[str]:
        captured["extract_selection"] = selection
        return ["函数"]

    async def fake_answer(query: str, memos: list[dict], selection: str = "") -> str:
        captured["answer_selection"] = selection
        return f"这段选中文本是一个计算平方根的实现。"

    with (
        patch.object(
            fast_chat,
            "ask_intent_and_delivery",
            return_value={"intent": "other", "delivery": "speak"},
        ),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(fast_chat, "_extract_keywords_with_harness", new=fake_extract),
        patch.object(fast_chat, "_answer_with_harness", new=fake_answer),
    ):
        result = await fast_chat.try_fast_memory_chat("这个函数是干嘛的", selected_text="def sqrt(x): return math.sqrt(x)")

    assert result is not None
    assert captured["answer_selection"] == "def sqrt(x): return math.sqrt(x)"
    assert "平方根" in result["reply"]
    assert result["action"] == "speak"
