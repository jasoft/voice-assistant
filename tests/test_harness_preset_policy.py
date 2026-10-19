from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
PRESET_ROOT = ROOT / "config" / "deepseek-harness" / "agent-presets"
CHAT_FAST_DIR = PRESET_ROOT / "chat-fast"
CHAT_FAST_PRESET = CHAT_FAST_DIR / "agent.cordis.yml"


class PresetYamlLoader(yaml.SafeLoader):
    """Safe loader that preserves Harness's JavaScript-tagged config values."""


PresetYamlLoader.add_constructor(
    "tag:yaml.org,2002:js",
    lambda loader, node: loader.construct_scalar(node),  # pyright: ignore[reportArgumentType]
)


def test_obsolete_presets_are_removed() -> None:
    assert not (PRESET_ROOT / "memo-mem0").exists()
    assert not (PRESET_ROOT / "memo-minimal").exists()
    assert (PRESET_ROOT / "chat-fast").exists()


def test_chat_fast_preset_blocks_all_skill_loading() -> None:
    preset_text = CHAT_FAST_PRESET.read_text(encoding="utf-8")
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
    assert "customSkillDirs" not in preset_text
    assert "mcp-client" not in preset_text
    assert "- id: tool-skill" not in preset_text
    assert not (CHAT_FAST_DIR / "skills").exists()


def test_chat_fast_preset_integrates_tools_and_commands() -> None:
    text = CHAT_FAST_PRESET.read_text(encoding="utf-8")

    assert "search_api.py" in text
    assert "weather_api.py" in text
    assert "memo_api.py" in text
    assert "禁止反复搜索" in text
    assert "bash" in text
    assert "不追问" in text
    assert "`python3 ${memoApi} add --text <完整用户原文>`" in text
    assert "`python3 ${memoApi} search --query <完整用户问题> --limit 3`" in text


def test_local_dsh_startup_pins_wrapper_and_refreshes_presets() -> None:
    text = (ROOT / "scripts" / "start_dsh.sh").read_text(encoding="utf-8")

    assert 'export MEMO_API_SCRIPT="${MEMO_API_SCRIPT:-$project_root/scripts/memo_api.py}"' in text
    assert 'local_preset_root="$project_root/config/deepseek-harness/agent-presets"' in text
    assert 'rm -rf "$dsh_home/.agent-presets/$preset_name"' in text
    assert 'cp -R "$preset_source" "$dsh_home/.agent-presets/$preset_name"' in text


def test_compose_uses_chat_fast_preset_and_mounts_presets() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert compose.count("PTT_HARNESS_AGENT_PRESET: chat-fast") == 2
    assert compose.count('PTT_HARNESS_TIMEOUT_SECONDS: "10"') == 1
    assert compose.count('PTT_HARNESS_TIMEOUT_SECONDS: "20"') == 1
    assert compose.count('PTT_CHAT_TIMEOUT_SECONDS: "45"') == 2
    assert 'PTT_HARNESS_ASYNC_TIMEOUT_SECONDS: "60"' in compose
    assert "./config/deepseek-harness/agent-presets/chat-fast:/root/.dsh/.agent-presets/chat-fast" in compose
    assert "memo-minimal" not in compose
    assert "memo-mem0" not in compose


def test_compose_allows_wrapper_execution_inside_disposable_harness_container() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "DSH_PERMISSION_MODE: danger-full-access" in compose


def test_deploy_defaults_to_fast_model() -> None:
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "./scripts/set_dsh_model.sh fast" in deploy
    assert "./scripts/set_dsh_model.sh free" not in deploy
    assert "./scripts/set_dsh_model.sh paid" not in deploy


def test_compose_and_api_wire_the_fast_chat_endpoint() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    api = (ROOT / "press_to_talk" / "api" / "main.py").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "api" / "v1.md").read_text(encoding="utf-8")

    assert "./config/deepseek-harness/agent-presets/chat-fast:/root/.dsh/.agent-presets/chat-fast" in compose
    assert 'PTT_CHAT_HARNESS_AGENT_PRESET: chat-fast' in compose
    assert 'PTT_CHAT_TIMEOUT_SECONDS: "45"' in compose
    assert 'MEM0_REQUEST_TIMEOUT_SECONDS: "3"' in compose
    assert 'DEFAULT_AGENT_PRESET = "chat-fast"' in api or 'agent_preset=os.environ.get("PTT_CHAT_HARNESS_AGENT_PRESET", "chat-fast")' in api
    assert "/v1/chat" in docs


