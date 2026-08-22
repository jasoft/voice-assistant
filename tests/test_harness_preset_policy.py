from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
PRESET_ROOT = ROOT / "config" / "deepseek-harness" / "agent-presets"
MINIMAL_DIR = PRESET_ROOT / "memo-minimal"
MINIMAL_PRESET = MINIMAL_DIR / "agent.cordis.yml"
MINIMAL_SKILL = MINIMAL_DIR / "skills" / "deepseek-harness" / "SKILL.md"
FALLBACK_PRESET = PRESET_ROOT / "memo-mem0" / "agent.cordis.yml"


def test_project_harness_preset_requires_explicit_record_intent() -> None:
    text = MINIMAL_PRESET.read_text(encoding="utf-8")

    assert "严格遵循它的公开 API" in text
    assert "不要维护第二份记忆库" in text


def test_minimal_preset_mounts_skill_support_and_bash_without_mcp() -> None:
    preset_text = MINIMAL_PRESET.read_text(encoding="utf-8")
    # PyYAML does not execute the Harness loader's intentional !!js expression.
    entries = yaml.safe_load(
        preset_text.replace(
            "!!js \"process.getBuiltinModule('node:url').fileURLToPath(new URL('skills/', baseUrl))\"",
            "skills",
        )
    )

    assert isinstance(entries, list)
    assert [entry["id"] for entry in entries] == ["persona", "skill-filesystem", "tool-skill", "memory-shell"]
    assert entries[1]["name"] == "@deepseek-ai/dsh-skill-filesystem"
    assert entries[2]["name"] == "@deepseek-ai/dsh-tool-skill"
    assert entries[3]["name"] == "@deepseek-ai/dsh-tool-bash"
    assert entries[3]["config"]["enableRunInBackground"] is False
    assert "mcp-client" not in MINIMAL_PRESET.read_text(encoding="utf-8")


def test_minimal_preset_installs_and_invokes_the_deepseek_harness_skill() -> None:
    text = MINIMAL_PRESET.read_text(encoding="utf-8")

    assert MINIMAL_SKILL.read_text(encoding="utf-8") == (ROOT / ".agents" / "skills" / "deepseek-harness" / "SKILL.md").read_text(encoding="utf-8")
    assert "先加载 deepseek-harness skill" in text
    assert "fileURLToPath(new URL('skills/', baseUrl))" in text
    assert "import.meta.url" not in text
    assert "/app/scripts/memo_api.py" not in text


def test_minimal_preset_uses_the_public_skill_api() -> None:
    text = MINIMAL_PRESET.read_text(encoding="utf-8")

    assert "curl" in (MINIMAL_SKILL.read_text(encoding="utf-8"))
    assert "PTT_API_KEY 已由运行环境提供给 bash" in text
    assert "memo_api.py" not in text


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
