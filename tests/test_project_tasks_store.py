"""项目任务存储（press_to_talk.project_tasks）定向测试。

覆盖：状态生命周期、幂等领取、追加要求续接、停止请求、所有者校验。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from press_to_talk import project_tasks as pt


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "project_tasks.json"


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    path = tmp_path / "projects.json"
    path.write_text(
        """{
  "projects": [
    {"id": "voice-assistant", "names": ["语音助手"], "path": "/tmp/va", "default_tool": "codex", "tools": ["codex", "antigravity"]},
    {"id": "comfyui", "names": ["千问", "comfyui"], "path": "/tmp/comfy", "default_tool": "codex", "tools": ["codex"]}
  ]
}""",
        encoding="utf-8",
    )
    return path


def _make_task(store_path: Path, **overrides):
    kwargs = dict(user_id="soj", project_id="voice-assistant", tool="codex", requirement="只读检查项目名和分支")
    kwargs.update(overrides)
    return pt.create_task(path=store_path, **kwargs)


def test_registry_public_entries_hide_paths(registry_path: Path):
    entries = pt.registry_public_entries(registry_path)
    assert [e["id"] for e in entries] == ["voice-assistant", "comfyui"]
    assert all("path" not in e for e in entries)
    voice = entries[0]
    assert "语音助手" in voice["names"] and "千问" not in voice["names"]
    assert voice["default_tool"] == "codex"


def test_resolve_project_and_alias_listing(registry_path: Path):
    assert pt.resolve_project("voice-assistant", registry_path)["path"] == "/tmp/va"
    assert pt.resolve_project("unknown-project", registry_path) is None


def test_create_and_claim_is_idempotent(store_path: Path):
    first = _make_task(store_path)
    second = _make_task(store_path)
    claimed = pt.claim_next_task(executor_id="mac-test", path=store_path)
    assert claimed is not None and claimed["id"] == first["id"]
    assert claimed["status"] == "running" and claimed["executor_id"] == "mac-test"
    # 第二次领取只能拿到下一个任务；重复领取不会重复发出第一个任务
    again = pt.claim_next_task(executor_id="mac-test", path=store_path)
    assert again is not None and again["id"] == second["id"]
    assert pt.claim_next_task(executor_id="mac-test", path=store_path) is None


def test_update_event_lifecycle_and_terminal_guard(store_path: Path):
    task = _make_task(store_path)
    pt.update_task_event(task["id"], status="running", native_session_id="01aabb-thread", path=store_path)
    updated = pt.update_task_event(task["id"], result="分支是 main", path=store_path)
    assert updated["native_session_id"] == "01aabb-thread"
    completed = pt.update_task_event(task["id"], status="completed", result="分支是 main", path=store_path)
    assert completed["status"] == "completed" and completed["finished_at"]
    # 终态后迟到的 running 事件不能复活任务
    late = pt.update_task_event(task["id"], status="running", path=store_path)
    assert late["status"] == "completed"
    with pytest.raises(ValueError):
        pt.update_task_event(task["id"], status="bogus", path=store_path)


def test_followup_requeues_terminal_task_same_session(store_path: Path):
    task = _make_task(store_path)
    pt.update_task_event(task["id"], status="completed", result="done", path=store_path)
    updated = pt.add_followup(task["id"], user_id="soj", text="回答尽量短一点", path=store_path)
    assert updated["status"] == "queued"
    assert updated["followups"] == ["回答尽量短一点"]
    assert updated["native_session_id"] is None or updated["native_session_id"] == task["native_session_id"]
    # 别的用户不能追加
    assert pt.add_followup(task["id"], user_id="someone-else", text="hack", path=store_path) is None


def test_followup_running_task_keeps_status(store_path: Path):
    task = _make_task(store_path)
    pt.claim_next_task(executor_id="mac-test", path=store_path)
    updated = pt.add_followup(task["id"], user_id="soj", text="补充要求", path=store_path)
    assert updated["status"] == "running"
    assert updated["followups"] == ["补充要求"]


def test_stop_request_flow(store_path: Path):
    queued = _make_task(store_path)
    cancelled = pt.request_stop(queued["id"], user_id="soj", path=store_path)
    assert cancelled["status"] == "cancelled"
    # 排队任务被取消后不会再被领取
    assert pt.claim_next_task(executor_id="mac-test", path=store_path) is None

    running = _make_task(store_path)
    pt.claim_next_task(executor_id="mac-test", path=store_path)
    flagged = pt.request_stop(running["id"], user_id="soj", path=store_path)
    assert flagged["stop_requested"] is True and flagged["status"] == "running"
    stopped = pt.update_task_event(running["id"], status="cancelled", path=store_path)
    assert stopped["stop_requested"] is False and stopped["status"] == "cancelled"
    assert pt.request_stop(running["id"], user_id="someone-else", path=store_path) is None


def test_owner_isolation(store_path: Path):
    task = _make_task(store_path, user_id="alice")
    assert pt.get_task(task["id"], path=store_path)["user_id"] == "alice"
    assert pt.list_tasks_for_user("bob", path=store_path) == []
    assert pt.latest_task_for_user("bob", path=store_path) is None
    assert pt.latest_task_for_user("alice", project_id="comfyui", path=store_path) is None
    assert pt.latest_task_for_user("alice", project_id="voice-assistant", path=store_path)["id"] == task["id"]


def test_claim_scoped_to_allowed_users(store_path: Path):
    _make_task(store_path, user_id="alice")
    # 执行器只配置了 bob：绝不能领到 alice 的任务
    assert pt.claim_next_task(executor_id="mac-x", path=store_path, allowed_user_ids={"bob"}) is None
    claimed = pt.claim_next_task(executor_id="mac-x", path=store_path, allowed_user_ids={"alice", "bob"})
    assert claimed is not None and claimed["user_id"] == "alice"


def test_stale_running_task_marked_failed_not_fake_running(store_path: Path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    task = _make_task(store_path)
    pt.record_executor_heartbeat(executor_id="mac-dead", path=store_path)
    pt.claim_next_task(executor_id="mac-dead", path=store_path)
    monkeypatch.setenv("PROJECT_TASK_STALE_RUNNING_SECONDS", "300")
    # 把 updated_at 与执行器心跳一起拨回 10 分钟前，模拟执行器彻底失联
    data = pt.load_tasks(store_path)
    old = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat(timespec="seconds")
    data["tasks"][0]["updated_at"] = old
    data["executors"]["mac-dead"]["last_seen"] = old
    pt.save_tasks(store_path, data)
    marked = pt.fail_stale_running_tasks(path=store_path)
    assert [t["id"] for t in marked] == [task["id"]]
    stored = pt.get_task(task["id"], path=store_path)
    assert stored["status"] == "failed"
    assert "执行器失联" in stored["error"]


def test_fresh_running_task_not_marked_stale(store_path: Path, monkeypatch):
    task = _make_task(store_path)
    pt.record_executor_heartbeat(executor_id="mac-alive", path=store_path)
    pt.claim_next_task(executor_id="mac-alive", path=store_path)
    monkeypatch.setenv("PROJECT_TASK_STALE_RUNNING_SECONDS", "300")
    assert pt.fail_stale_running_tasks(path=store_path) == []
    assert pt.get_task(task["id"], path=store_path)["status"] == "running"


def test_followup_requeues_waiting_task(store_path: Path):
    """waiting（如原生会话被原工具占用）的任务被再次语音追加时应重新入队。"""
    task = _make_task(store_path)
    pt.update_task_event(
        task["id"], status="waiting",
        note="原生会话正被原工具界面占用", path=store_path,
    )
    updated = pt.add_followup(task["id"], user_id="soj", text="再试一次", path=store_path)
    assert updated["status"] == "queued"
    assert updated["note"] is None
    assert updated["followups"] == ["再试一次"]
