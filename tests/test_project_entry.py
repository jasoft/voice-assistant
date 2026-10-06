"""项目任务语义入口（press_to_talk.api.project_entry）定向测试。

只测纯逻辑 handle_project_task_result（LLM 解析在外部 mock），
覆盖：new/continue/status/stop/clarify、登记项目约束、诚实回执文案。
"""

from __future__ import annotations

import pytest

from press_to_talk.api import project_entry
from press_to_talk.project_tasks import configured_store_path, load_tasks


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_PROJECT_TASK_STORE_PATH", str(tmp_path / "tasks.json"))
    registry = tmp_path / "projects.json"
    registry.write_text(
        """{
  "projects": [
    {"id": "voice-assistant", "names": ["语音助手"], "path": "/tmp/va", "default_tool": "codex", "tools": ["codex", "antigravity"]},
    {"id": "comfyui", "names": ["千问", "comfyui"], "path": "/tmp/comfy", "default_tool": "codex", "tools": ["codex"]}
  ]
}""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PTT_PROJECT_REGISTRY_PATH", str(registry))
    return tmp_path / "tasks.json"


def _handle(parsed, **kwargs):
    kwargs.setdefault("query", "测试原话")
    kwargs.setdefault("user_id", "soj")
    return project_entry.handle_project_task_result(parsed, **kwargs)


def test_new_task_creates_record_and_reports_queued(store_env, monkeypatch):
    monkeypatch.setenv("PROJECT_EXECUTOR_ONLINE_WINDOW_SECONDS", "0")
    result = _handle({"action": "new", "project_id": "voice-assistant", "tool": None, "requirement": "给语音助手加上任务进度查询"})
    assert result is not None and result["action"] == "speak"
    assert "已登记" in result["reply"] and "离线" in result["reply"]  # 执行器离线时不得谎称已开始
    tasks = load_tasks(store_env)["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["tool"] == "codex" and tasks[0]["project_id"] == "voice-assistant"
    assert result["debug_info"]["task_action"] == "new"


def test_new_task_respects_user_tool_choice(store_env):
    _handle({"action": "new", "project_id": "voice-assistant", "tool": "antigravity", "requirement": "修一下日志"})
    tasks = load_tasks(store_env)["tasks"]
    assert tasks[0]["tool"] == "antigravity"


def test_new_task_rejects_unknown_project(store_env):
    result = _handle({"action": "new", "project_id": "not-registered", "tool": None, "requirement": "x"})
    assert result is not None
    assert "没有登记" in result["reply"]
    assert result["debug_info"]["delegated"] is False
    assert load_tasks(store_env)["tasks"] == []


def test_explicit_unavailable_tool_never_silently_swapped(store_env):
    # 用户明确点名 Antigravity，但 comfyui 只启用了 codex：必须明确告知不可用，
    # 不能静默换成 Codex 执行
    result = _handle({"action": "new", "project_id": "comfyui", "tool": "antigravity", "requirement": "修一下图片拖拽"})
    assert result is not None
    assert "没有启用" in result["reply"] and "Antigravity" in result["reply"]
    assert result["debug_info"]["delegated"] is False
    tasks = load_tasks(store_env)["tasks"]
    assert tasks == []  # 未创建任何任务，更没有偷偷用 codex 转交


def test_tool_marked_unavailable_in_registry_is_refused(store_env, monkeypatch):
    # 注册表 tool_availability 标记 antigravity 不可用（未通过原工具列表验收）：
    # 即使项目启用了它，入口也必须拒绝并说明，不能建任务后由执行器失败
    registry_path = store_env.parent / "projects.json"
    data = __import__("json").loads(registry_path.read_text(encoding="utf-8"))
    data["tool_availability"] = {"codex": True, "antigravity": False}
    registry_path.write_text(__import__("json").dumps(data, ensure_ascii=False), encoding="utf-8")
    result = _handle({"action": "new", "project_id": "voice-assistant", "tool": "antigravity", "requirement": "修日志"})
    assert result is not None
    assert "没有通过原工具列表验收" in result["reply"]
    assert result["debug_info"]["delegated"] is False and result["debug_info"]["tool_unavailable"] is True
    assert load_tasks(store_env)["tasks"] == []


def test_new_task_without_requirement_falls_back(store_env):
    assert _handle({"action": "new", "project_id": "voice-assistant", "requirement": None}) is None


def test_clarify_returns_question(store_env):
    result = _handle({"action": "clarify", "clarification": "要在哪个项目里做？"})
    assert result["reply"] == "要在哪个项目里做？"


def test_status_reports_real_state(store_env):
    _handle({"action": "new", "project_id": "comfyui", "requirement": "只读检查"})
    from press_to_talk.project_tasks import update_task_event, configured_store_path

    task = load_tasks(store_env)["tasks"][0]
    update_task_event(task["id"], status="completed", result="custom_nodes 共 12 个", path=configured_store_path())
    result = _handle({"action": "status", "project_id": "comfyui"})
    assert "已完成" in result["reply"] and "12 个" in result["reply"]


def test_status_without_history(store_env):
    result = _handle({"action": "status"})
    assert "没有登记过" in result["reply"]


def test_continue_appends_followup(store_env):
    _handle({"action": "new", "project_id": "voice-assistant", "requirement": "只读检查分支"})
    task = load_tasks(store_env)["tasks"][0]
    from press_to_talk.project_tasks import update_task_event, configured_store_path

    update_task_event(task["id"], status="running", path=configured_store_path())
    result = _handle({"action": "continue", "requirement": "回答尽量短一点"})
    assert "补充要求" in result["reply"]
    updated = load_tasks(store_env)["tasks"][0]
    assert updated["followups"] == ["回答尽量短一点"]
    assert updated["status"] == "running"


def test_stop_running_sets_flag(store_env):
    _handle({"action": "new", "project_id": "voice-assistant", "requirement": "长任务"})
    from press_to_talk.project_tasks import claim_next_task, update_task_event, configured_store_path

    task = load_tasks(store_env)["tasks"][0]
    claim_next_task(executor_id="mac-test", path=configured_store_path())
    update_task_event(task["id"], status="running", path=configured_store_path())
    result = _handle({"action": "stop"})
    assert "停止请求" in result["reply"]
    assert load_tasks(store_env)["tasks"][0]["stop_requested"] is True


def test_stop_without_tasks(store_env):
    result = _handle({"action": "stop"})
    assert "没有找到" in result["reply"]


def test_none_action_falls_back(store_env):
    assert _handle({"action": "none"}) is None
    assert _handle({"action": "unknown"}) is None


def test_idempotency_key_dedups_within_window(store_env, monkeypatch):
    first = project_entry_handle_new_with_key(store_env, monkeypatch)
    second = project_entry_handle_new_with_key(store_env, monkeypatch)
    assert first["id"] == second["id"]
    assert len(load_tasks(store_env)["tasks"]) == 1


def project_entry_handle_new_with_key(store_env, monkeypatch):
    from press_to_talk.project_tasks import create_task

    return create_task(
        user_id="soj",
        project_id="voice-assistant",
        tool="codex",
        requirement="同一个需求",
        path=store_env,
        idempotency_key="retry-key-1",
    )


def test_same_request_id_retry_after_long_time_never_reexecutes(store_env):
    # 请求身份永久去重：网络重试无论间隔多久都不能再次执行原任务
    from datetime import datetime, timedelta, timezone

    from press_to_talk.project_tasks import create_task, load_tasks, save_tasks

    first = create_task(
        user_id="soj", project_id="voice-assistant", tool="codex",
        requirement="同一个需求", path=store_env, idempotency_key="chat:soj:req-1",
    )
    # 模拟两小时后客户端带着同一 request_id 重试
    data = load_tasks(store_env)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    data["tasks"][0]["created_at"] = old
    save_tasks(store_env, data)
    retried = create_task(
        user_id="soj", project_id="voice-assistant", tool="codex",
        requirement="同一个需求", path=store_env, idempotency_key="chat:soj:req-1",
    )
    assert retried["id"] == first["id"]
    assert len(load_tasks(store_env)["tasks"]) == 1


def test_new_request_id_within_window_allows_explicit_repeat(store_env):
    # 用户明确重新发起（客户端生成新 request_id）：即使几秒后、原话相同也新建任务
    from press_to_talk.project_tasks import create_task, load_tasks

    create_task(
        user_id="soj", project_id="voice-assistant", tool="codex",
        requirement="同一个需求", path=store_env, idempotency_key="chat:soj:req-1",
    )
    second = create_task(
        user_id="soj", project_id="voice-assistant", tool="codex",
        requirement="同一个需求", path=store_env, idempotency_key="chat:soj:req-2",
    )
    assert len(load_tasks(store_env)["tasks"]) == 2
    assert second["id"] != load_tasks(store_env)["tasks"][0]["id"]


def test_idempotency_key_is_per_user(store_env, monkeypatch):
    from press_to_talk.project_tasks import create_task, load_tasks

    create_task(
        user_id="alice", project_id="voice-assistant", tool="codex",
        requirement="需求", path=store_env, idempotency_key="shared-key",
    )
    create_task(
        user_id="bob", project_id="voice-assistant", tool="codex",
        requirement="需求", path=store_env, idempotency_key="shared-key",
    )
    assert len(load_tasks(store_env)["tasks"]) == 2


def test_chat_request_id_threaded_to_create_task(store_env, monkeypatch):
    """/v1/chat 链路：request_id 从入口传到任务创建（幂等键 = chat:<user>:<request_id>）。"""
    import asyncio

    from unittest.mock import AsyncMock, patch

    from press_to_talk.api import project_entry

    async def fake_parse(query, registry, recent):
        return {"action": "new", "project_id": "voice-assistant", "tool": None, "requirement": "给语音助手加进度查询"}

    monkeypatch.setenv("PROJECT_EXECUTOR_ONLINE_WINDOW_SECONDS", "0")
    with patch.object(project_entry, "_parse_with_direct_llm", new=AsyncMock(side_effect=fake_parse)):
        first = asyncio.run(project_entry.handle_project_task_intent(
            "让Codex给语音助手加上进度查询", user_id="soj", request_id="req-abc"))
        second = asyncio.run(project_entry.handle_project_task_intent(
            "让Codex给语音助手加上进度查询", user_id="soj", request_id="req-abc"))
    assert first is not None and second is not None
    assert first["debug_info"]["task_id"] == second["debug_info"]["task_id"]
    assert len(load_tasks(store_env)["tasks"]) == 1
    assert load_tasks(store_env)["tasks"][0]["idempotency_key"] == "chat:soj:req-abc"
