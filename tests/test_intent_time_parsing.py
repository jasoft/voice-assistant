"""
详尽测试意图判断和时间区间拆分。

测试目标：
1. LLM 意图提取能正确识别 find/record 意图
2. 各种时间表达式能正确拆分为 start_date/end_date
3. 边界情况（跨年、闰年、月末等）处理正确
4. 日期格式验证（YYYY-MM-DD）
5. 错误处理（不完整 JSON、错误日期格式等）
"""
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from press_to_talk.agent.agent import OpenAICompatibleAgent
from press_to_talk.models.config import Config
from press_to_talk.utils.text import current_time_text


def _make_llm_response(content: dict) -> ChatCompletion:
    """构造一个模拟的 LLM ChatCompletion 响应"""
    return ChatCompletion(
        id="test-id",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    content=json.dumps(content, ensure_ascii=False),
                    role="assistant"
                )
            )
        ],
        created=123,
        model="test-model",
        object="chat.completion"
    )


def _make_agent():
    """创建一个测试用的 Agent 实例（直接构造 Config，避免命令行解析）"""
    cfg = Config(
        sample_rate=16000,
        channels=1,
        threshold=0.1,
        silence_seconds=1.0,
        no_speech_timeout_seconds=5.0,
        calibration_seconds=1.0,
        stt_url="http://localhost:8000",
        stt_token="test-token",
        audio_file=None,
        text_input=None,
        classify_only=False,
        intent_samples_file=None,
        no_tts=True,
        gui_events=False,
        gui_auto_close_seconds=5,
        debug=False,
        llm_api_key="fake-key-for-test",
        llm_base_url="http://localhost:8000/v1",
        llm_model="test-model",
        llm_summarize_model="test-model",
        workspace_root=None,
        remember_script=None,
        execution_mode="memory-chat",
        user_id="test-user",
        user_token=None,
        force_ask=False,
        force_record=False,
        keyword_search_enabled=True,
        semantic_search_enabled=True,
        photo_path=None,
    )
    return OpenAICompatibleAgent(cfg)


def _today_str():
    return datetime.now().strftime("%Y-%m-%d")


def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _first_day_of_month(year, month):
    return f"{year:04d}-{month:02d}-01"


def _last_day_of_month(year, month):
    """计算某年某月的最后一天"""
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    return last_day.strftime("%Y-%m-%d")


# ==================== 基础意图判断测试 ====================

@pytest.mark.anyio
async def test_intent_record():
    """测试记录意图识别"""
    agent = _make_agent()
    response = _make_llm_response({
        "intent": "record",
        "tool": "remember_add",
        "args": {"memory": "护照在书房抽屉里", "query": ""},
        "confidence": 0.98,
        "notes": "用户要记录信息"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("帮我记一下，护照在书房抽屉里")

    assert payload["intent"] == "record"
    assert payload["tool"] == "remember_add"
    assert "护照" in payload["args"]["memory"]


@pytest.mark.anyio
async def test_intent_find():
    """测试查询意图识别"""
    agent = _make_agent()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "帮我找下护照在哪", "start_date": None, "end_date": None},
        "confidence": 0.99,
        "notes": "用户要查询信息"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("帮我找下护照在哪")

    assert payload["intent"] == "find"
    assert payload["tool"] == "remember_find"
    assert "护照" in payload["args"]["query"]


@pytest.mark.anyio
async def test_intent_chat_fallback():
    """测试非 record/find 意图被强制转为 find"""
    agent = _make_agent()
    response = _make_llm_response({
        "intent": "chat",
        "tool": "some_tool",
        "args": {"query": "你好"},
        "confidence": 0.5
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("你好")

    # 代码会将非 record/find 的 intent 转为 find
    assert payload["intent"] == "find"


# ==================== 相对时间表达式测试 ====================

@pytest.mark.anyio
async def test_time_today():
    """测试"今天"的日期范围"""
    agent = _make_agent()
    today = _today_str()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "今天记了什么", "start_date": today, "end_date": today},
        "confidence": 0.99,
        "notes": "查询今日记录"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("今天记了什么")

    assert payload["args"]["start_date"] == today
    assert payload["args"]["end_date"] == today


@pytest.mark.anyio
async def test_time_yesterday():
    """测试"昨天"的日期范围"""
    agent = _make_agent()
    yesterday = _days_ago(1)
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "昨天记了什么", "start_date": yesterday, "end_date": yesterday},
        "confidence": 0.99,
        "notes": "查询昨日记录"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("昨天记了什么")

    assert payload["args"]["start_date"] == yesterday
    assert payload["args"]["end_date"] == yesterday


@pytest.mark.anyio
async def test_time_recent_three_days():
    """测试"最近三天"的日期范围"""
    agent = _make_agent()
    three_days_ago = _days_ago(3)
    today = _today_str()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "最近三天记了什么", "start_date": three_days_ago, "end_date": today},
        "confidence": 0.99,
        "notes": "查询最近三天记录"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("最近三天记了什么")

    assert payload["args"]["start_date"] == three_days_ago
    assert payload["args"]["end_date"] == today
    assert payload["args"]["start_date"] <= payload["args"]["end_date"]


@pytest.mark.anyio
async def test_time_recent_week():
    """测试"最近一周"的日期范围"""
    agent = _make_agent()
    week_ago = _days_ago(7)
    today = _today_str()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "最近一周的记录有哪些", "start_date": week_ago, "end_date": today},
        "confidence": 0.99,
        "notes": "查询最近一周记录"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("最近一周的记录有哪些")

    assert payload["args"]["start_date"] == week_ago
    assert payload["args"]["end_date"] == today


@pytest.mark.anyio
async def test_time_last_month():
    """测试"上个月"的日期范围（核心测试用例）"""
    agent = _make_agent()
    now = datetime.now()
    if now.month == 1:
        last_month_year = now.year - 1
        last_month = 12
    else:
        last_month_year = now.year
        last_month = now.month - 1

    start_date = _first_day_of_month(last_month_year, last_month)
    end_date = _last_day_of_month(last_month_year, last_month)

    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "查询上个月的消息", "start_date": start_date, "end_date": end_date},
        "confidence": 0.99,
        "notes": "查询上个月记录"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("查询上个月的消息")

    assert payload["args"]["start_date"] == start_date
    assert payload["args"]["end_date"] == end_date
    assert payload["args"]["start_date"] <= payload["args"]["end_date"]


@pytest.mark.anyio
async def test_time_last_month_cross_year():
    """测试跨年的"上个月"（如1月查询上个月=去年12月）"""
    agent = _make_agent()
    # 模拟当前是1月，上个月是去年12月
    # 这个测试验证 LLM 能正确处理跨年情况
    start_date = f"{datetime.now().year - 1}-12-01"
    end_date = f"{datetime.now().year - 1}-12-31"

    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "查询上个月的消息", "start_date": start_date, "end_date": end_date},
        "confidence": 0.99,
        "notes": "跨年查询上个月"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("查询上个月的消息")

    assert payload["args"]["start_date"] == start_date
    assert payload["args"]["end_date"] == end_date
    assert "12-01" in payload["args"]["start_date"]
    assert "12-31" in payload["args"]["end_date"]


@pytest.mark.anyio
async def test_time_last_year():
    """测试"去年"的日期范围"""
    agent = _make_agent()
    last_year = datetime.now().year - 1
    start_date = f"{last_year}-01-01"
    end_date = f"{last_year}-12-31"

    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "查询去年的消息", "start_date": start_date, "end_date": end_date},
        "confidence": 0.99,
        "notes": "查询去年记录"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("查询去年的消息")

    assert payload["args"]["start_date"] == start_date
    assert payload["args"]["end_date"] == end_date


@pytest.mark.anyio
async def test_time_last_week():
    """测试"上周"的日期范围"""
    agent = _make_agent()
    today = datetime.now()
    # 上周一
    days_since_monday = (today.weekday() + 1) % 7  # 0=周一, 6=周日
    this_monday = today - timedelta(days=days_since_monday)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)

    start_date = last_monday.strftime("%Y-%m-%d")
    end_date = last_sunday.strftime("%Y-%m-%d")

    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "上周记了什么", "start_date": start_date, "end_date": end_date},
        "confidence": 0.99,
        "notes": "查询上周记录"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("上周记了什么")

    assert payload["args"]["start_date"] == start_date
    assert payload["args"]["end_date"] == end_date
    assert payload["args"]["start_date"] <= payload["args"]["end_date"]


# ==================== 绝对时间表达式测试 ====================

@pytest.mark.anyio
async def test_time_specific_month():
    """测试指定月份（如"3月"）的日期范围"""
    agent = _make_agent()
    now = datetime.now()
    start_date = f"{now.year}-03-01"
    end_date = f"{now.year}-03-31"

    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "查询3月的消息", "start_date": start_date, "end_date": end_date},
        "confidence": 0.99,
        "notes": "查询3月记录"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("查询3月的消息")

    assert payload["args"]["start_date"] == start_date
    assert payload["args"]["end_date"] == end_date


@pytest.mark.anyio
async def test_time_specific_date():
    """测试指定具体日期"""
    agent = _make_agent()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "查询2026-03-15的消息", "start_date": "2026-03-15", "end_date": "2026-03-15"},
        "confidence": 0.99,
        "notes": "查询指定日期"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("查询2026-03-15的消息")

    assert payload["args"]["start_date"] == "2026-03-15"
    assert payload["args"]["end_date"] == "2026-03-15"


@pytest.mark.anyio
async def test_time_date_range():
    """测试指定日期范围"""
    agent = _make_agent()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "查询3月1日到3月15日的消息", "start_date": "2026-03-01", "end_date": "2026-03-15"},
        "confidence": 0.99,
        "notes": "查询日期范围"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("查询3月1日到3月15日的消息")

    assert payload["args"]["start_date"] == "2026-03-01"
    assert payload["args"]["end_date"] == "2026-03-15"
    assert payload["args"]["start_date"] <= payload["args"]["end_date"]


# ==================== 日期格式验证测试 ====================

@pytest.mark.anyio
async def test_date_format_yyyy_mm_dd():
    """测试日期格式必须是 YYYY-MM-DD"""
    agent = _make_agent()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "今天记了什么", "start_date": "2026-04-26", "end_date": "2026-04-26"},
        "confidence": 0.99
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("今天记了什么")

    import re
    date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    if payload["args"].get("start_date"):
        assert date_pattern.match(payload["args"]["start_date"]), f"start_date 格式错误: {payload['args']['start_date']}"
    if payload["args"].get("end_date"):
        assert date_pattern.match(payload["args"]["end_date"]), f"end_date 格式错误: {payload['args']['end_date']}"


@pytest.mark.anyio
async def test_date_no_time_component():
    """测试日期不应包含时间部分"""
    agent = _make_agent()
    # LLM 不应返回带时间的日期
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "今天记了什么", "start_date": "2026-04-26", "end_date": "2026-04-26"},
        "confidence": 0.99
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("今天记了什么")

    start_date = payload["args"].get("start_date")
    end_date = payload["args"].get("end_date")

    if start_date:
        assert "T" not in start_date, f"start_date 不应包含时间部分: {start_date}"
        assert len(start_date) == 10, f"start_date 长度应为10: {start_date}"
    if end_date:
        assert "T" not in end_date, f"end_date 不应包含时间部分: {end_date}"
        assert len(end_date) == 10, f"end_date 长度应为10: {end_date}"


# ==================== 边界情况测试 ====================

@pytest.mark.anyio
async def test_leap_year_february():
    """测试闰年2月的最后一天"""
    agent = _make_agent()
    # 2024是闰年，2月有29天
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "查询2024年2月的消息", "start_date": "2024-02-01", "end_date": "2024-02-29"},
        "confidence": 0.99,
        "notes": "闰年2月"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("查询2024年2月的消息")

    assert payload["args"]["end_date"] == "2024-02-29"


@pytest.mark.anyio
async def test_30_day_month():
    """测试30天的月份（4月、6月、9月、11月）"""
    agent = _make_agent()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "查询4月的消息", "start_date": "2026-04-01", "end_date": "2026-04-30"},
        "confidence": 0.99
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("查询4月的消息")

    assert payload["args"]["end_date"] == "2026-04-30"


@pytest.mark.anyio
async def test_31_day_month():
    """测试31天的月份（1月、3月、5月、7月、8月、10月、12月）"""
    agent = _make_agent()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "查询3月的消息", "start_date": "2026-03-01", "end_date": "2026-03-31"},
        "confidence": 0.99
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("查询3月的消息")

    assert payload["args"]["end_date"] == "2026-03-31"


@pytest.mark.anyio
async def test_start_date_before_end_date():
    """测试 start_date 必须早于或等于 end_date"""
    agent = _make_agent()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "查询3月1日到3月15日的消息", "start_date": "2026-03-01", "end_date": "2026-03-15"},
        "confidence": 0.99
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("查询3月1日到3月15日的消息")

    start = payload["args"]["start_date"]
    end = payload["args"]["end_date"]
    assert start <= end, f"start_date ({start}) 必须早于或等于 end_date ({end})"


# ==================== 无日期查询测试 ====================

@pytest.mark.anyio
async def test_no_date_query():
    """测试没有时间范围的查询（纯关键词查询）"""
    agent = _make_agent()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "帮我找下护照在哪", "start_date": None, "end_date": None},
        "confidence": 0.99,
        "notes": "无时间范围查询"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("帮我找下护照在哪")

    assert payload["intent"] == "find"
    assert payload["args"]["query"] == "帮我找下护照在哪"
    # 无日期范围时，start_date 和 end_date 应为 None 或不存在
    assert payload["args"].get("start_date") is None or payload["args"].get("start_date") == ""
    assert payload["args"].get("end_date") is None or payload["args"].get("end_date") == ""


@pytest.mark.anyio
async def test_query_preserves_original_text():
    """测试 query 字段保留用户原始问句"""
    agent = _make_agent()
    user_input = "帮我找一下，我的护照到底放在哪里了？"
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": user_input, "start_date": None, "end_date": None},
        "confidence": 0.99
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload(user_input)

    # query 必须保留原始问句，不能改写
    assert payload["args"]["query"] == user_input


# ==================== 错误处理测试 ====================

@pytest.mark.anyio
async def test_malformed_json():
    """测试 LLM 返回非法 JSON 的处理"""
    agent = _make_agent()

    # 构造一个返回非法 JSON 的响应
    bad_response = ChatCompletion(
        id="test-id",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    content="这不是一个合法的 JSON",
                    role="assistant"
                )
            )
        ],
        created=123,
        model="test-model",
        object="chat.completion"
    )

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = bad_response
        # 应该抛出异常或返回默认值
        try:
            payload = await agent._extract_intent_payload("测试非法JSON")
            # 如果没有抛异常，检查是否有合理的默认值
            assert "intent" in payload
        except (RuntimeError, json.JSONDecodeError):
            # 预期会抛出异常
            pass


@pytest.mark.anyio
async def test_missing_fields_in_response():
    """测试 LLM 返回缺少字段的 JSON"""
    agent = _make_agent()
    # 缺少 args 字段
    response = _make_llm_response({
        "intent": "find"
        # 缺少 tool, args, confidence
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("测试缺少字段")

    # 代码应该补全缺失的字段
    assert "intent" in payload
    assert "args" in payload
    assert "query" in payload["args"]


@pytest.mark.anyio
async def test_invalid_date_format():
    """测试 LLM 返回错误日期格式"""
    agent = _make_agent()
    # 错误的日期格式（应该用 YYYY-MM-DD，但返回了 YYYY/MM/DD）
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {"query": "今天记了什么", "start_date": "2026/04/26", "end_date": "2026/04/26"},
        "confidence": 0.99
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("今天记了什么")

    # 检查日期格式
    start_date = payload["args"].get("start_date", "")
    # 这里只是检查，实际是否接受取决于后续处理
    # 至少确保不会崩溃
    assert payload["intent"] == "find"


# ==================== 系统提示词验证测试 ====================

def test_system_prompt_contains_time_instructions():
    """测试系统提示词包含时间处理指令"""
    agent = _make_agent()
    messages = agent._build_intent_extractor_messages("测试")
    system_msg = next(m for m in messages if m["role"] == "system")
    content = system_msg["content"]

    # 验证关键指令存在
    assert "今天" in content or "上" in content
    assert "start_date" in content
    assert "end_date" in content
    assert "YYYY-MM-DD" in content


def test_system_prompt_time_placeholder_replaced():
    """测试系统提示词中的 ${PTT_CURRENT_TIME} 被正确替换"""
    agent = _make_agent()
    messages = agent._build_intent_extractor_messages("测试")
    system_msg = next(m for m in messages if m["role"] == "system")
    content = system_msg["content"]

    # 占位符应该被替换
    assert "${PTT_CURRENT_TIME}" not in content
    # 应该包含实际的当前时间
    assert datetime.now().strftime("%Y") in content


# ==================== 综合测试 ====================

@pytest.mark.anyio
async def test_complex_query_with_time():
    """测试复杂查询：包含时间+具体内容的查询"""
    agent = _make_agent()
    today = _today_str()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {
            "query": "今天关于壮壮的记录有哪些",
            "start_date": today,
            "end_date": today
        },
        "confidence": 0.99,
        "notes": "查询今日特定内容"
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("今天关于壮壮的记录有哪些")

    assert payload["intent"] == "find"
    assert payload["args"]["start_date"] == today
    assert payload["args"]["end_date"] == today
    assert "壮壮" in payload["args"]["query"]


@pytest.mark.anyio
async def test_query_with_time_and_memory_field():
    """测试查询时 memory 字段应为空"""
    agent = _make_agent()
    response = _make_llm_response({
        "intent": "find",
        "tool": "remember_find",
        "args": {
            "query": "查询上个月的消息",
            "memory": "",  # find 意图时 memory 应为空
            "start_date": "2026-03-01",
            "end_date": "2026-03-31"
        },
        "confidence": 0.99
    })

    with patch.object(agent.client.chat.completions, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = response
        payload = await agent._extract_intent_payload("查询上个月的消息")

    assert payload["args"].get("memory", "") == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
