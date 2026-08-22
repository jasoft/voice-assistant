from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import press_to_talk.api.main as api_main
from press_to_talk.api.auth import get_user_id
from press_to_talk.storage.models import RememberItemRecord, SessionHistoryRecord
from press_to_talk.storage.providers.mem0 import Mem0RememberStore


def _fake_history_service(records: list[SessionHistoryRecord]):
    calls: list[SessionHistoryRecord] = []
    history_store = SimpleNamespace(
        persist=calls.append,
        list_recent=lambda *, limit: records[:limit],
    )
    service = SimpleNamespace(history_store=lambda: history_store)
    return service, calls


def test_harness_query_persists_user_and_reply_to_pocketbase(monkeypatch) -> None:
    monkeypatch.setenv("PTT_QUERY_BACKEND", "deepseek-harness")
    monkeypatch.setattr(api_main, "base_config", SimpleNamespace())
    api_main.app.dependency_overrides[get_user_id] = lambda: "test-user"

    fake_client = AsyncMock()
    fake_client.query.return_value = {
        "reply": "测试回复",
        "memories": [],
        "images": [],
        "query": "测试问题",
        "debug_info": {"backend": "deepseek-harness", "session_id": "session-1"},
    }
    history_service, persisted = _fake_history_service([])
    monkeypatch.setattr(api_main, "_harness_client_for", lambda _user_id: fake_client)
    monkeypatch.setattr(api_main, "_history_service_for", lambda _user_id: history_service)

    try:
        with TestClient(api_main.app) as client:
            response = client.post(
                "/v1/query",
                json={"query": "测试问题", "mode": "memory-chat"},
            )
    finally:
        api_main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["reply"] == "测试回复"
    fake_client.query.assert_awaited_once_with("测试问题", photo=None)
    assert len(persisted) == 1
    assert isinstance(persisted[0], SessionHistoryRecord)
    assert persisted[0].transcript == "测试问题"
    assert persisted[0].reply == "测试回复"
    assert persisted[0].mode == "memory-chat"
    assert persisted[0].session_id.startswith("session-1:")


def test_harness_history_reads_recent_pocketbase_records(monkeypatch) -> None:
    monkeypatch.setenv("PTT_QUERY_BACKEND", "deepseek-harness")
    monkeypatch.setattr(api_main, "base_config", SimpleNamespace())
    api_main.app.dependency_overrides[get_user_id] = lambda: "test-user"

    records = [
        SessionHistoryRecord(
            session_id="session-1:3",
            started_at="2026-08-22T10:00:00+08:00",
            transcript="测试问题",
            reply="测试回复",
            mode="memory-chat",
        )
    ]
    history_service, persisted = _fake_history_service(records)
    monkeypatch.setattr(api_main, "_history_service_for", lambda _user_id: history_service)

    try:
        with TestClient(api_main.app) as client:
            response = client.post("/v1/history")
    finally:
        api_main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "session_id": "session-1:3",
            "transcript": "测试问题",
            "reply": "测试回复",
            "created_at": "2026-08-22T10:00:00+08:00",
        }
    ]
    assert persisted == []


def test_harness_memories_read_all_mem0_records_in_time_order(monkeypatch) -> None:
    monkeypatch.setenv("PTT_QUERY_BACKEND", "deepseek-harness")
    monkeypatch.setattr(api_main, "base_config", SimpleNamespace())
    api_main.app.dependency_overrides[get_user_id] = lambda: "test-user"

    records = [
        RememberItemRecord(id="old", memory="旧记忆", created_at="2026-08-20T01:00:00+00:00"),
        RememberItemRecord(id="new", memory="新记忆", created_at="2026-08-21T01:00:00+00:00"),
        RememberItemRecord(id="invalid", memory="无效时间", created_at="not-a-time"),
    ]
    fake_store = SimpleNamespace(list_all_records=lambda: records.copy())
    monkeypatch.setattr(api_main, "_mem0_store_for", lambda _user_id: fake_store)

    try:
        with TestClient(api_main.app) as client:
            response = client.post("/v1/memories")
    finally:
        api_main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["new", "old", "invalid"]
    assert [item["memory"] for item in response.json()] == ["新记忆", "旧记忆", "无效时间"]


def test_mem0_full_listing_preserves_original_iso_timestamps() -> None:
    store = Mem0RememberStore(client=object(), user_id="test-user")
    store.get_all = lambda: [  # type: ignore[method-assign]
        {
            "id": "memory-1",
            "memory": "原始时间记忆",
            "created_at": "2026-08-21T09:30:00.000Z",
            "updated_at": "2026-08-21T10:00:00.000Z",
        }
    ]

    records = store.list_all_records()

    assert len(records) == 1
    assert records[0].created_at == "2026-08-21T09:30:00.000Z"
    assert records[0].updated_at == "2026-08-21T10:00:00.000Z"


def test_harness_query_returns_500_when_history_persistence_fails(monkeypatch) -> None:
    monkeypatch.setenv("PTT_QUERY_BACKEND", "deepseek-harness")
    monkeypatch.setattr(api_main, "base_config", SimpleNamespace())
    api_main.app.dependency_overrides[get_user_id] = lambda: "test-user"

    fake_client = AsyncMock()
    fake_client.query.return_value = {"reply": "成功", "query": "问题"}
    failing_store = SimpleNamespace(persist=lambda _record: (_ for _ in ()).throw(RuntimeError("db down")))
    failing_service = SimpleNamespace(history_store=lambda: failing_store)
    monkeypatch.setattr(api_main, "_harness_client_for", lambda _user_id: fake_client)
    monkeypatch.setattr(api_main, "_history_service_for", lambda _user_id: failing_service)

    try:
        with TestClient(api_main.app) as client:
            response = client.post("/v1/query", json={"query": "问题"})
    finally:
        api_main.app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()["detail"] == "查询成功但写入会话历史失败"
    fake_client.query.assert_awaited_once()


def test_set_dsh_model_updates_default_model_before_deployment(tmp_path) -> None:
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        'agent-default-model:\n  model: old\n  other: keep\nother:\n  model: untouched\n',
        encoding="utf-8",
    )

    import subprocess

    result = subprocess.run(
        ["./scripts/set_dsh_model.sh", "free"],
        env={"DSH_SETTINGS": str(settings), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert settings.read_text(encoding="utf-8") == (
        'agent-default-model:\n  model: free\n  other: keep\nother:\n  model: untouched\n'
    )
    deploy_script = __import__("pathlib").Path("scripts/deploy.sh").read_text(encoding="utf-8")
    model_position = deploy_script.find("./scripts/set_dsh_model.sh free")
    compose_position = deploy_script.find("docker compose up -d --build")
    assert model_position != -1
    assert compose_position != -1
    assert model_position < compose_position
