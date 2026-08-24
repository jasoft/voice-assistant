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

    assert "只有用户明确要求" in text
    assert "完整用户原文" in text
    assert "必须通过 bash 工具调用执行" in text
    assert "绝对不能把命令文本直接作为回复输出" in text


def test_minimal_preset_blocks_all_skill_loading() -> None:
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
    assert entries[2]["name"] == "@deepseek-ai/dsh-skill-filesystem"
    assert entries[2]["config"]["includeDefaultRoots"] is False
    # No customSkillDirs: loading any skill risks the model following
    # remember/SKILL.md and calling /v1/query on itself.
    assert "customSkillDirs" not in preset_text
    assert "mcp-client" not in preset_text
    assert "- id: tool-skill" not in preset_text
    # The remember skill must not ship inside this preset; external callers
    # should use .agents/skills/remember/SKILL.md instead.
    assert not (MINIMAL_DIR / "skills").exists()


def test_minimal_preset_uses_direct_compact_mem0_wrapper() -> None:
    text = MINIMAL_PRESET.read_text(encoding="utf-8")

    assert "process.env.MEMO_API_SCRIPT" in text
    assert '"/app/scripts/memo_api.py"' in text
    assert "`python3 ${memoApi} add --text <完整用户原文>`" in text
    assert "`python3 ${memoApi} search --query <完整用户问题> --limit 3`" in text
    assert "`python3 ${memoApi} list --page 1 --page-size 20`" in text
    assert "`python3 ${memoApi} delete --id <memory-id>`" in text
    assert "--limit 3" in text
    assert "完整保留 Mem0 返回的记忆内容" in text


def test_local_dsh_startup_pins_wrapper_and_refreshes_presets() -> None:
    text = (ROOT / "scripts" / "start_dsh.sh").read_text(encoding="utf-8")

    assert 'export MEMO_API_SCRIPT="${MEMO_API_SCRIPT:-$project_root/scripts/memo_api.py}"' in text
    assert 'local_preset_root="$project_root/config/deepseek-harness/agent-presets"' in text
    assert 'rm -rf "$dsh_home/.agent-presets/$preset_name"' in text
    assert 'cp -R "$preset_source" "$dsh_home/.agent-presets/$preset_name"' in text


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


def test_fast_chat_preset_routes_memory_and_brave_without_skills() -> None:
    chat_dir = PRESET_ROOT / "chat-fast"
    entries = yaml.load(
        (chat_dir / "agent.cordis.yml").read_text(encoding="utf-8"),
        Loader=PresetYamlLoader,
    )

    assert [entry["id"] for entry in entries] == [
        "persona",
        "memory-shell",
        "tool-web",
        "skill-filesystem",
    ]
    persona = entries[0]["config"]["text"]
    web = entries[2]["config"]
    assert "明确要求记住、记录、保存、记一下" in persona
    assert "search --query <完整用户问题> --limit 5" in persona
    assert "web_search" in persona
    assert "不追问" in persona
    assert entries[1]["name"] == "@deepseek-ai/dsh-tool-bash"
    assert entries[3]["name"] == "@deepseek-ai/dsh-skill-filesystem"
    assert entries[3]["config"]["includeDefaultRoots"] is False
    assert web["fetch"] is False
    assert web["searchMaxResults"] == 3
    assert web["searchTimeoutMs"] <= 3500
    assert not (chat_dir / "skills").exists()


def test_compose_and_api_wire_the_fast_chat_endpoint() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    api = (ROOT / "press_to_talk" / "api" / "main.py").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "api" / "v1.md").read_text(encoding="utf-8")

    assert "./config/deepseek-harness/agent-presets/chat-fast:/root/.dsh/.agent-presets/chat-fast" in compose
    assert 'PTT_CHAT_HARNESS_AGENT_PRESET: chat-fast' in compose
    assert 'PTT_CHAT_TIMEOUT_SECONDS: "30"' in compose
    assert 'MEM0_REQUEST_TIMEOUT_SECONDS: "3"' in compose
    assert '@app.post(\n    "/v1/chat"' in api
    assert '@app.post(\n    "/chat"' in api
    assert "_chat_harness_client_for(user_id)" in api
    assert "POST /v1/chat" in docs


def test_base_bundle_mounts_native_time_context() -> None:
    patch = (ROOT / "deepseek-harness" / "packages" / "bundle" / "base" / "cordis.patch.yml").read_text(
        encoding="utf-8"
    )
    package = (ROOT / "deepseek-harness" / "packages" / "bundle" / "base" / "package.json").read_text(
        encoding="utf-8"
    )

    assert "- id: time-context" in patch
    assert "@deepseek-ai/dsh-time-context" in patch
    assert "timeZone: Asia/Shanghai" in patch
    assert '"@deepseek-ai/dsh-time-context": "workspace:^"' in package
    # The one-shot chat persona intentionally suppresses runtime context, so the
    # native clock plugin must remain independent at the base bundle layer.
    assert "includeRuntimeContext: false" in (PRESET_ROOT / "chat-fast" / "agent.cordis.yml").read_text(
        encoding="utf-8"
    )
