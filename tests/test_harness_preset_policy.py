from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
PRESET_ROOT = ROOT / "config" / "deepseek-harness" / "agent-presets"
MINIMAL_DIR = PRESET_ROOT / "memo-minimal"
MINIMAL_PRESET = MINIMAL_DIR / "agent.cordis.yml"
FALLBACK_PRESET = PRESET_ROOT / "memo-mem0" / "agent.cordis.yml"


class PresetYamlLoader(yaml.SafeLoader):
    """Safe loader that preserves Harness's JavaScript-tagged config values."""


PresetYamlLoader.add_constructor(
    "tag:yaml.org,2002:js",
    lambda loader, node: loader.construct_scalar(node),
)


def test_project_harness_preset_requires_explicit_record_intent() -> None:
    text = MINIMAL_PRESET.read_text(encoding="utf-8")

    assert "只有用户明确要求“记住、记录、保存、记一下”时才写入" in text
    assert "完整用户原文" in text
    assert "必须通过 bash 工具执行" in text
    assert "不要把命令文本作为最终回复" in text


def test_minimal_preset_discovers_only_preset_directory_skills() -> None:
    preset_text = MINIMAL_PRESET.read_text(encoding="utf-8")
    entries = yaml.load(preset_text, Loader=PresetYamlLoader)

    assert isinstance(entries, list)
    assert [entry["id"] for entry in entries] == [
        "persona",
        "memory-shell",
        "skill-filesystem",
    ]
    assert entries[1]["name"] == "@deepseek-ai/dsh-tool-bash"
    assert entries[1]["config"]["enableRunInBackground"] is False
    assert entries[1]["config"]["concludeOnSuccessCommandPrefixes"] == [
        "python3 /app/scripts/memo_api.py add ",
        "python3 /app/scripts/memo_api.py delete ",
    ]
    assert entries[2]["name"] == "@deepseek-ai/dsh-skill-filesystem"
    assert entries[2]["config"]["includeDefaultRoots"] is False
    assert entries[2]["config"]["customSkillDirs"] == [
        "process.getBuiltinModule('node:url').fileURLToPath(new URL('skills/', baseUrl))",
    ]
    assert "mcp-client" not in preset_text
    assert "- id: tool-skill" not in preset_text
    skill = MINIMAL_DIR / "skills" / "remember" / "SKILL.md"
    assert skill.read_text(encoding="utf-8").startswith("---\nname: remember\n")


def test_minimal_preset_uses_direct_compact_mem0_wrapper() -> None:
    text = MINIMAL_PRESET.read_text(encoding="utf-8")

    assert "python3 /app/scripts/memo_api.py add" in text
    assert "python3 /app/scripts/memo_api.py search" in text
    assert "python3 /app/scripts/memo_api.py list" in text
    assert "python3 /app/scripts/memo_api.py delete" in text
    assert "--limit 3" in text
    assert "最终回复不超过 60 个汉字" in text


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
    assert compose.count('PTT_HARNESS_TIMEOUT_SECONDS: "10"') == 2
    assert 'PTT_HARNESS_ASYNC_TIMEOUT_SECONDS: "60"' in compose
    assert "./config/deepseek-harness/agent-presets/memo-minimal:/root/.dsh/.agent-presets/memo-minimal" in compose
    assert "./config/deepseek-harness/agent-presets/memo-mem0:/root/.dsh/.agent-presets/memo-mem0" in compose


def test_compose_allows_wrapper_execution_inside_disposable_harness_container() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "DSH_PERMISSION_MODE: danger-full-access" in compose


def test_deploy_defaults_to_fast_model() -> None:
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "./scripts/set_dsh_model.sh fast" in deploy
    assert "./scripts/set_dsh_model.sh free" not in deploy
