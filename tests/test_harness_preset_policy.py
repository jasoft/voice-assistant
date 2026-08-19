from pathlib import Path


PRESET = Path(__file__).parents[1] / "config" / "deepseek-harness" / "agent-presets" / "memo-mem0" / "agent.cordis.yml"


def test_project_harness_preset_requires_explicit_record_intent() -> None:
    text = PRESET.read_text(encoding="utf-8")

    assert "只有用户在当前消息中明确说" in text
    assert "绝不能自行记录" in text
    assert "如果意图不清楚" in text
    assert "不要因为信息看起来有价值就推断用户想保存" in text


def test_project_harness_preset_has_multi_pass_recall_rules() -> None:
    text = PRESET.read_text(encoding="utf-8")

    assert "第一次用完整用户问题检索" in text
    assert "最多两轮" in text
    assert "get_memories + user_id 过滤" in text
    assert "人名、地名、日期、数字、书名和位置尽量逐字保留原文" in text


def test_project_harness_preset_does_not_contain_a_literal_mem0_token() -> None:
    text = PRESET.read_text(encoding="utf-8")

    assert "MEM0_MCP_TOKEN" in text
    assert "Authorization: !!js" in text
    assert "Token <" not in text
