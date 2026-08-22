from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
PRESET_ROOT = ROOT / "config" / "deepseek-harness" / "agent-presets"
MINIMAL_PRESET = PRESET_ROOT / "memo-minimal" / "agent.cordis.yml"
FALLBACK_PRESET = PRESET_ROOT / "memo-mem0" / "agent.cordis.yml"


def test_project_harness_preset_requires_explicit_record_intent() -> None:
    text = MINIMAL_PRESET.read_text(encoding="utf-8")

    assert "只有原话明确包含" in text
    assert "查询过去事实先 search" in text


def test_minimal_preset_mounts_only_bash_without_mcp() -> None:
    entries = yaml.safe_load(MINIMAL_PRESET.read_text(encoding="utf-8"))

    assert isinstance(entries, list)
    assert [entry["id"] for entry in entries] == ["persona", "memory-shell"]
    assert entries[1]["name"] == "@deepseek-ai/dsh-tool-bash"
    assert entries[1]["config"]["enableRunInBackground"] is False
    assert "mcp-client" not in MINIMAL_PRESET.read_text(encoding="utf-8")


def test_minimal_preset_pins_exact_mem0_wrapper_commands() -> None:
    text = MINIMAL_PRESET.read_text(encoding="utf-8")

    assert "python3 /app/scripts/memo_api.py add --text <原文>" in text
    assert "python3 /app/scripts/memo_api.py search --query <问题> --limit 5" in text
    assert "python3 /app/scripts/memo_api.py list --page 1 --page-size 10" in text


def test_project_harness_preset_has_multi_pass_recall_rules() -> None:
    text = FALLBACK_PRESET.read_text(encoding="utf-8")

    assert "第一次用完整用户问题检索" in text
    assert "最多两轮" in text
    assert "get_memories + user_id 过滤" in text
    assert "人名、地名、日期、数字、书名和位置尽量逐字保留原文" in text


def test_project_harness_preset_does_not_contain_a_literal_mem0_token() -> None:
    text = FALLBACK_PRESET.read_text(encoding="utf-8")

    assert "MEM0_MCP_TOKEN" in text
    assert "Authorization: !!js" in text
    assert "Token <" not in text


def test_compose_uses_minimal_preset_and_mounts_presets() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert compose.count("PTT_HARNESS_AGENT_PRESET: memo-minimal") == 2
    assert "./config/deepseek-harness/agent-presets/memo-minimal:/root/.dsh/.agent-presets/memo-minimal" in compose
    assert "./config/deepseek-harness/agent-presets/memo-mem0:/root/.dsh/.agent-presets/memo-mem0" in compose


def test_compose_allows_wrapper_execution_inside_disposable_harness_container() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "DSH_PERMISSION_MODE: danger-full-access" in compose


def test_deploy_defaults_to_free_model() -> None:
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "./scripts/set_dsh_model.sh free" in deploy
    assert "./scripts/set_dsh_model.sh fast" not in deploy
