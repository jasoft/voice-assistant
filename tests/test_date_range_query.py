import json
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from press_to_talk.storage.providers.sqlite_fts import SQLiteFTS5RememberStore
from press_to_talk.agent.agent import OpenAICompatibleAgent
from press_to_talk.models.config import Config

@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_memory.sqlite3"
    store = SQLiteFTS5RememberStore(db_path=db_path)
    return store

def test_sqlite_date_range_query(temp_db):
    # 模拟过去的时间比较难，因为 add 内部写死了 datetime.now()
    # 我们先插入记录，然后手动修改日期
    temp_db.add(memory="一周前的记录")
    
    import sqlite3
    conn = sqlite3.connect(temp_db.db_path)
    try:
        # 修改一条记录到一周前
        last_week = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
        conn.execute("UPDATE remember_entries SET created_at = ? WHERE memory = ?", (last_week, "一周前的记录"))
        conn.commit()
    finally:
        conn.close()
    
    # 现在连接已经关闭，可以继续使用 temp_db.add
    temp_db.add(memory="今天的记录")
    
    # 1. 查询今天
    today_str = datetime.now().strftime("%Y-%m-%d")
    res_today = json.loads(temp_db.find(query="", start_date=today_str, end_date=today_str))
    assert len(res_today["results"]) == 1
    assert res_today["results"][0]["memory"] == "今天的记录"
    
    # 2. 查询最近一周
    last_week_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    res_week = json.loads(temp_db.find(query="", start_date=last_week_date, end_date=today_str))
    assert len(res_week["results"]) == 2

@pytest.mark.anyio
async def test_agent_date_extraction():
    # 测试 Agent 是否能从“今天记了什么”中提取出日期
    from press_to_talk.models.config import parse_args
    cfg = parse_args(["--api-key", "fake", "--base-url", "http://localhost:8000/v1", "--model", "fast"])
    agent = OpenAICompatibleAgent(cfg)
    
    # 模拟 LLM 返回
    import unittest.mock as mock
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice
    
    mock_response = ChatCompletion(
        id="id",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    content=json.dumps({
                        "intent": "find",
                        "tool": "remember_find",
                        "args": {
                            "query": "我今天记了什么？",
                            "start_date": "2026-04-20",
                            "end_date": "2026-04-20"
                        },
                        "confidence": 0.99,
                        "notes": "用户查询当日记忆"
                    }),
                    role="assistant"
                )
            )
        ],
        created=123,
        model="fast",
        object="chat.completion"
    )
    
    with mock.patch.object(agent.client.chat.completions, "create", new_callable=mock.AsyncMock) as mock_create:
        mock_create.return_value = mock_response
        payload = await agent._extract_intent_payload("我今天记了什么？")
        assert payload["args"]["start_date"] == "2026-04-20"
        assert payload["args"]["end_date"] == "2026-04-20"
