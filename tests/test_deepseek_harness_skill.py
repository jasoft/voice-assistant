from pathlib import Path


SKILL = Path(__file__).parents[1] / ".agents" / "skills" / "deepseek-harness" / "SKILL.md"


def test_skill_documents_the_current_harness_rpc_contract() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "/api/session.create" in text
    assert "/api/session.prompt" in text
    assert "/api/session.history" in text
    assert '"type": "client-request"' in text
    assert '"mode": "queue"' in text
    assert 'event.type == "assistant/message"' in text
    assert "turn/end" in text


def test_skill_keeps_harness_as_the_single_memory_owner() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "PTT_QUERY_BACKEND=deepseek-harness" in text
    assert "memo-mem0" in text
    assert '{"AND":[{"user_id":"soj"}]}' in text
    assert "infer=false" in text
    assert "不要绕过 Harness 直接访问 Mem0" in text


def test_skill_does_not_contain_a_literal_credential() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "<caller-api-key>" in text
    assert "<base64-or-data-uri>" in text
    assert "Token <" not in text
