"""项目任务 HTTP 端点测试：/v1/project-tasks*。

覆盖：认证、创建校验（未登记项目/未启用工具）、所有者 404、领取幂等、事件回传。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from press_to_talk.api.auth import get_user_id
from press_to_talk.api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
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
    app.dependency_overrides[get_user_id] = lambda: "soj"
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_endpoints_require_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_PROJECT_TASK_STORE_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setenv("PTT_PROJECT_REGISTRY_PATH", str(tmp_path / "projects.json"))
    app.dependency_overrides.pop(get_user_id, None)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/v1/project-tasks", json={"project_id": "voice-assistant", "requirement": "x"})
    assert resp.status_code in (401, 403)


def test_create_rejects_unregistered_project(client):
    resp = client.post("/v1/project-tasks", json={"project_id": "not-registered", "requirement": "做点事"})
    assert resp.status_code == 400


def test_create_rejects_tool_not_enabled_for_project(client):
    resp = client.post(
        "/v1/project-tasks",
        json={"project_id": "comfyui", "tool": "antigravity", "requirement": "修拖拽"},
    )
    assert resp.status_code == 400


def test_full_task_lifecycle_over_http(client):
    created = client.post(
        "/v1/project-tasks",
        json={"project_id": "voice-assistant", "requirement": "只读检查项目名和分支，不修改文件"},
    )
    assert created.status_code == 201
    task = created.json()
    assert task["status"] == "queued" and task["tool"] == "codex" and task["native_session_id"] is None

    # 列表与查询（所有者 soj）
    listed = client.get("/v1/project-tasks").json()
    assert [t["id"] for t in listed] == [task["id"]]

    # 执行器领取 → running
    claimed = client.post(
        "/v1/project-tasks/claim",
        json={"executor_id": "mac-test", "tools": ["codex", "antigravity"]},
    )
    assert claimed.status_code == 200
    assert claimed.json()["id"] == task["id"] and claimed.json()["status"] == "running"
    # 并发/重复领取不能再拿到同一任务
    empty = client.post("/v1/project-tasks/claim", json={"executor_id": "mac-test"})
    assert empty.json() is None

    # 回传原生会话 id 与结果
    event = client.post(
        f"/v1/project-tasks/{task['id']}/events",
        json={"status": "completed", "native_session_id": "01aabb-thread", "result": "voice-assistant / main"},
    )
    assert event.status_code == 200
    final = client.get(f"/v1/project-tasks/{task['id']}").json()
    assert final["status"] == "completed"
    assert final["native_session_id"] == "01aabb-thread"
    assert final["result"] == "voice-assistant / main"


def test_owner_isolation_over_http(client, monkeypatch):
    created = client.post(
        "/v1/project-tasks",
        json={"project_id": "voice-assistant", "requirement": "需求"},
    )
    task_id = created.json()["id"]
    # 切换身份后 404（不泄露任务存在性）
    app.dependency_overrides[get_user_id] = lambda: "intruder"
    try:
        assert client.get(f"/v1/project-tasks/{task_id}").status_code == 404
        assert client.post(f"/v1/project-tasks/{task_id}/followup", json={"text": "x"}).status_code == 404
        assert client.post(f"/v1/project-tasks/{task_id}/stop").status_code == 404
    finally:
        app.dependency_overrides[get_user_id] = lambda: "soj"


def test_followup_and_stop_over_http(client):
    created = client.post(
        "/v1/project-tasks",
        json={"project_id": "comfyui", "requirement": "只读列出自定义节点数量"},
    )
    task_id = created.json()["id"]
    followed = client.post(f"/v1/project-tasks/{task_id}/followup", json={"text": "结果用一句话回答"})
    assert followed.status_code == 200
    assert followed.json()["followups"] == ["结果用一句话回答"]

    stopped = client.post(f"/v1/project-tasks/{task_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "cancelled"
