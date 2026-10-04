"""Fast-chat harness chain tests for try_fast_memory_chat.

Mocks TypeSafe and the DeepSeek Harness chat-fast calls; exercises the
拆词 → 一次 CEL → 回答 chain, the record branch, the paste (compose) branch,
and the TypeSafe-failure fallback (None).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from press_to_talk.api import fast_chat


@pytest.fixture(autouse=True)
def isolate_harness_tests_from_live_completions():
    """Harness tests must reach their mocked Harness, never the live fast model."""
    with (
        patch.object(fast_chat, "_answer_with_direct_llm", new=AsyncMock(return_value=None)),
        patch.object(fast_chat, "_compose_with_direct_llm", new=AsyncMock(return_value=None)),
    ):
        yield


class _FakeMemos:
    """Minimal MemosClient stand-in used by _build_memos_client mock."""

    def __init__(self, items: list[dict]):
        self.items = items
        self.created: list[str] = []

    def create_memo(self, content: str, visibility: str = "PRIVATE") -> dict:
        self.created.append(content)
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
        patch.object(fast_chat, "_summarize_record_content", new=AsyncMock(return_value="护照在书房")) as summarize,
    ):
        result = await fast_chat.try_fast_memory_chat("帮我记一下护照在书房")
    assert result is not None
    assert result["action"] == "speak"
    assert result["debug_info"]["intent"] == "record"
    assert "已记入" in result["reply"]
    summarize.assert_awaited_once_with("帮我记一下护照在书房", "")
    assert fake.created == ["护照在书房\n\n> 语音原文: 帮我记一下护照在书房\n\n#voice"]


@pytest.mark.anyio
async def test_record_summary_failure_does_not_write_or_fall_back():
    fake = _FakeMemos([])
    with (
        patch.object(fast_chat, "ask_intent_and_delivery", return_value={"intent": "record", "delivery": "paste"}),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(fast_chat, "_summarize_record_content", new=AsyncMock(side_effect=TimeoutError("summary timeout"))),
    ):
        result = await fast_chat.try_fast_memory_chat("帮我记录一下，今天做了一个手表 App。")
    assert result is not None
    assert result["debug_info"]["stage"] == "record_summary"
    assert "记录失败" in result["reply"]
    assert fake.created == []


@pytest.mark.anyio
@pytest.mark.parametrize("content,finish_reason,valid", [
    ("今天做了一个手表 App，保存了录音记录。", "stop", True),
    ("", "stop", False),
    ("   ", "stop", False),
    ("今天做了一个", "length", False),
])
async def test_record_chat_completion_uses_full_original_and_selection(content, finish_reason, valid):
    query = "帮我记录一下，今天做了一个手表 App，保存了录音记录。"
    selection = "设计草稿"
    response = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason, message=SimpleNamespace(content=content),
    )])
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    client.__aenter__.return_value = client
    with (
        patch("openai.AsyncOpenAI", return_value=client),
        patch.object(fast_chat, "_prompt", return_value="外部配置的语义整理提示词") as prompt,
    ):
        if valid:
            assert await fast_chat._summarize_record_content(query, selection) == content
        else:
            with pytest.raises(ValueError):
                await fast_chat._summarize_record_content(query, selection)
    prompt.assert_called_once_with("memo_record_summary")
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    import json
    source = json.loads(messages[1]["content"])
    assert source["original_text"] == query
    assert source["selected_text"] == selection
    client.__aexit__.assert_awaited_once()


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
    assert result["debug_info"]["intent"] == "query"
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


@pytest.mark.anyio
async def test_harness_answer_and_compose_inject_current_time(monkeypatch):
    monkeypatch.setenv("PTT_CURRENT_TIME", "2026-10-03 23:00:00 星期六")
    captured_prompts = []

    class _FakeClient:
        async def query(self, prompt: str):
            captured_prompts.append(prompt)
            return {"reply": "ok"}

        async def close(self):
            pass

    with patch.object(fast_chat, "_chat_harness_client", return_value=_FakeClient()):
        ans = await fast_chat._answer_with_harness("今天是星期几", [])
        assert ans == "ok"
        assert "2026-10-03 23:00:00 星期六" in captured_prompts[0]

        captured_prompts.clear()
        comp = await fast_chat._compose_with_harness("写一封邮件", "")
        assert comp == "ok"
        assert "2026-10-03 23:00:00 星期六" in captured_prompts[0]


@pytest.mark.anyio
async def test_typesafe_debug_info_included_in_fast_chat_result():
    """验证 fast_chat 返回的 debug_info 中包含完整的 typesafe 裁决详情与打分。"""
    fake = _FakeMemos([])
    ts_decision = {
        "intent": "chat",
        "delivery": "speak",
        "details": {
            "model": "clef-flash",
            "intent": {
                "choice": "chat",
                "confidence": 0.95,
                "probabilities": {"record": 0.01, "query": 0.02, "chat": 0.95, "agent": 0.02},
            },
            "delivery": {
                "choice": "speak",
                "confidence": 0.98,
                "probabilities": {"paste": 0.02, "speak": 0.98},
            },
        },
    }

    async def fake_answer(query: str, memos: list[dict], selection: str = "") -> str:
        return "今天星期一。"

    with (
        patch.object(fast_chat, "ask_intent_and_delivery", return_value=ts_decision),
        patch.object(fast_chat, "_build_memos_client", return_value=fake),
        patch.object(fast_chat, "_answer_with_direct_llm", return_value="今天星期一。"),
    ):
        result = await fast_chat.try_fast_memory_chat("今天星期几？")

    assert result is not None
    assert "debug_info" in result
    debug = result["debug_info"]
    assert "typesafe" in debug
    ts = debug["typesafe"]
    assert ts["model"] == "clef-flash"
    assert ts["intent"]["choice"] == "chat"
    assert ts["intent"]["confidence"] == 0.95
    assert ts["intent"]["probabilities"]["chat"] == 0.95
    assert ts["delivery"]["choice"] == "speak"
    assert ts["delivery"]["confidence"] == 0.98
    assert ts["delivery"]["probabilities"]["speak"] == 0.98

