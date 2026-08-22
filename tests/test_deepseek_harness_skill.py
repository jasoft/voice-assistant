from pathlib import Path


SKILL = Path(__file__).parents[1] / ".agents" / "skills" / "deepseek-harness" / "SKILL.md"


def test_skill_documents_only_the_public_note_and_query_contract() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "$BASE/v1/query" in text
    assert "$BASE/v1/history" in text
    assert "$BASE/v1/memories" in text
    assert 'Authorization: Bearer <PTT_API_KEY>' in text
    assert "用户想保存时" in text
    assert "不要替用户改写" in text


def test_skill_does_not_expose_internal_apis_or_configuration() -> None:
    text = SKILL.read_text(encoding="utf-8").lower()

    forbidden_markers = (
        "/api/session.",
        "session.create",
        "session.prompt",
        "session.history",
        "rpcid",
        "ptt_harness_api_url",
        "ptt_query_backend",
        "memo-mem0",
        "deepseekharnessclient",
        "mem0_api_key",
        "dsh_home",
    )
    assert not any(marker in text for marker in forbidden_markers)


def test_skill_defines_the_note_query_boundary() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "/v1/query" in text
    assert "/v1/history" in text
    assert "/v1/memories" in text
    assert "不要在客户端维护第二份记忆库" in text
    assert "只有明确要求“记住”“记录”“保存”时才应新增记忆" in text


def test_skill_does_not_contain_a_literal_credential() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "<PTT_API_KEY>" in text
    assert "Token <" not in text
