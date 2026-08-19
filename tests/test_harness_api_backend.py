from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import press_to_talk.api.main as api_main
from press_to_talk.api.auth import get_user_id


def test_harness_backend_history_and_memories_do_not_use_pocketbase(monkeypatch) -> None:
    monkeypatch.setenv("PTT_QUERY_BACKEND", "deepseek-harness")
    api_main.app.dependency_overrides[get_user_id] = lambda: "test-user"

    fake_client = AsyncMock()
    fake_client.list_history.return_value = [{
        "session_id": "session-1:3",
        "transcript": "测试问题",
        "reply": "测试回复",
        "created_at": "2026-08-20T10:00:00+08:00",
    }]
    monkeypatch.setattr(api_main, "_harness_client_for", lambda _user_id: fake_client)

    try:
        with TestClient(api_main.app) as client:
            history = client.post("/v1/history")
            memories = client.post("/v1/memories")
    finally:
        api_main.app.dependency_overrides.clear()

    assert history.status_code == 200
    assert history.json()[0]["reply"] == "测试回复"
    assert memories.status_code == 200
    assert memories.json() == []
    fake_client.list_history.assert_awaited_once_with(limit=20)
