from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import press_to_talk.api.main as api_main
from press_to_talk.api.auth import get_user_id
from press_to_talk.harness import DeepSeekHarnessClient, HarnessError
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


def _clear_query_jobs() -> None:
    for job in api_main._query_jobs.values():
        if job.task is not None and not job.task.done():
            job.task.cancel()
    api_main._query_jobs.clear()


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
    client = SimpleNamespace(
        get_all=lambda **_kwargs: [
        {
            "id": "memory-1",
            "memory": "原始时间记忆",
            "created_at": "2026-08-21T09:30:00.000Z",
            "updated_at": "2026-08-21T10:00:00.000Z",
        }
        ]
    )
    store = Mem0RememberStore(client=client, user_id="test-user")

    records = store.list_all_records()

    assert len(records) == 1
    assert records[0].created_at == "2026-08-21T09:30:00.000Z"
    assert records[0].updated_at == "2026-08-21T10:00:00.000Z"


def test_mem0_full_listing_follows_v2_pagination(monkeypatch) -> None:
    pages = [
        {
            "count": 128,
            "results": [
                {
                    "id": f"memory-{number}",
                    "memory": f"记忆 {number}",
                    "created_at": "2026-08-21T09:30:00.000Z",
                }
                for number in range(100)
            ],
        },
        {
            "count": 128,
            "results": [
                {
                    "id": f"memory-{number}",
                    "memory": f"记忆 {number}",
                    "created_at": "2026-08-21T10:30:00.000Z",
                }
                for number in range(28)
            ],
        },
    ]
    requested_pages: list[int] = []

    def fake_get_all(**kwargs) -> dict[str, object]:
        page = kwargs["page"]
        requested_pages.append(page)
        assert kwargs["page_size"] == 100
        assert kwargs["filters"] == {
            "OR": [
                {"AND": [{"user_id": "test-user"}]},
                {
                    "AND": [
                        {"user_id": "test-user"},
                        {"OR": [{"app_id": "*"}, {"agent_id": "*"}]},
                    ]
                },
            ]
        }
        return pages[page - 1]

    client = SimpleNamespace(get_all=fake_get_all)
    store = Mem0RememberStore(client=client, user_id="test-user")
    records = store.list_all_records()

    assert requested_pages == [1, 2]
    assert len(records) == 128


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


def test_async_query_returns_immediately_and_polls_to_success(monkeypatch) -> None:
    monkeypatch.setenv("PTT_QUERY_BACKEND", "deepseek-harness")
    monkeypatch.setenv("PTT_HARNESS_ASYNC_TIMEOUT_SECONDS", "300")
    monkeypatch.setattr(api_main, "base_config", SimpleNamespace())
    _clear_query_jobs()
    api_main.app.dependency_overrides[get_user_id] = lambda: "test-user"

    fake_client = AsyncMock()
    fake_client.query.return_value = {
        "reply": "后台回复",
        "memories": [],
        "images": [],
        "query": "后台问题",
        "debug_info": {"session_id": "session-async"},
    }
    history_service, persisted = _fake_history_service([])
    monkeypatch.setattr(api_main, "_harness_client_for", lambda _user_id: fake_client)
    monkeypatch.setattr(api_main, "_history_service_for", lambda _user_id: history_service)

    try:
        with TestClient(api_main.app) as client:
            response = client.post("/v1/query/async", json={"query": "后台问题"})
            assert response.status_code == 202
            accepted = response.json()
            job_id = accepted["job_id"]
            assert accepted["status"] == "queued"
            assert accepted["status_url"] == f"/v1/query/status/{job_id}"

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                status = client.get(f"/v1/query/status/{job_id}")
                assert status.status_code == 200
                if status.json()["status"] == "succeeded":
                    break
                time.sleep(0.01)
            status = client.get(f"/v1/query/status/{job_id}")
    finally:
        api_main.app.dependency_overrides.clear()
        _clear_query_jobs()

    assert status.json()["status"] == "succeeded"
    assert status.json()["reply"] == "后台回复"
    fake_client.query.assert_awaited_once_with(
        "后台问题", photo=None, timeout_seconds=300.0
    )
    assert len(persisted) == 1
    assert persisted[0].reply == "后台回复"


def test_async_query_reports_failure_without_history_write(monkeypatch) -> None:
    monkeypatch.setenv("PTT_QUERY_BACKEND", "deepseek-harness")
    monkeypatch.setattr(api_main, "base_config", SimpleNamespace())
    _clear_query_jobs()
    api_main.app.dependency_overrides[get_user_id] = lambda: "test-user"

    fake_client = AsyncMock()
    fake_client.query.side_effect = HarnessError("等待超时")
    history_service, persisted = _fake_history_service([])
    monkeypatch.setattr(api_main, "_harness_client_for", lambda _user_id: fake_client)
    monkeypatch.setattr(api_main, "_history_service_for", lambda _user_id: history_service)

    try:
        with TestClient(api_main.app) as client:
            response = client.post("/v1/query/async", json={"query": "失败问题"})
            job_id = response.json()["job_id"]
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                status = client.get(f"/v1/query/status/{job_id}")
                if status.json()["status"] == "failed":
                    break
                time.sleep(0.01)
            unknown = client.get("/v1/query/status/not-a-job")
    finally:
        api_main.app.dependency_overrides.clear()
        _clear_query_jobs()

    assert response.status_code == 202
    assert status.json()["status"] == "failed"
    assert "等待超时" in status.json()["error"]
    assert persisted == []
    assert unknown.status_code == 404


def test_harness_client_uses_async_timeout_override() -> None:
    async def check() -> None:
        client = DeepSeekHarnessClient("http://deepseek-harness.invalid")
        client._ensure_session = AsyncMock(return_value="session-1")  # type: ignore[method-assign]
        client._rpc = AsyncMock(return_value={})  # type: ignore[method-assign]
        client._history = AsyncMock(return_value=[])  # type: ignore[method-assign]
        client._wait_for_reply = AsyncMock(return_value="OK")  # type: ignore[method-assign]

        result = await client.query("测试", timeout_seconds=300)

        assert result["reply"] == "OK"
        client._wait_for_reply.assert_awaited_once_with(
            "session-1", -1, timeout_seconds=300
        )
        await client.close()

    asyncio.run(check())


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
