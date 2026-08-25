from fastapi.testclient import TestClient

from press_to_talk.api import main as api_main
from press_to_talk.api.auth import get_user_id


def _client(monkeypatch):
    monkeypatch.setattr(api_main, "base_config", object(), raising=False)
    api_main.app.dependency_overrides[get_user_id] = lambda: "test-user"
    return TestClient(api_main.app)


def test_create_list_and_cancel_remote_reminders(tmp_path, monkeypatch):
    store = tmp_path / "reminders.json"
    monkeypatch.setenv("QSTASH_URL", "https://qstash.test")
    monkeypatch.setenv("QSTASH_TOKEN", "token")
    monkeypatch.setenv("BARK_URL", "https://bark.test/key")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("PTT_MODEL", "model")
    monkeypatch.setenv("PTT_REMINDER_STORE_PATH", str(store))

    record = {
        "id": "5A3D6B7C-8E29-4F0A-9D1B-2F6A7B8C9D0E",
        "qstash_message_id": "msg_123",
        "message": "看电脑",
        "scheduled_at": "2026-08-25T17:50:00+00:00",
        "created_at": "2026-08-25T17:48:00+00:00",
        "status": "scheduled",
        "is_recurring": False,
        "cron_expression": None,
        "timezone_identifier": None,
        "schedule_description": None,
    }

    def fake_create(*args, **kwargs):
        from press_to_talk.reminders import save_reminder_records
        assert kwargs["store_path"] == store
        save_reminder_records(store, [record])
        return {"kind": "once", "message": "看电脑", "remote_id": "msg_123"}

    cancelled = []
    monkeypatch.setattr(api_main, "create_reminder_from_text", fake_create)
    monkeypatch.setattr(api_main, "cancel_remote_reminder", lambda message_id, **kwargs: cancelled.append(message_id))

    with _client(monkeypatch) as client:
        created = client.post("/v1/reminders", json={"text": "两分钟后提醒我看电脑"})
        assert created.status_code == 201
        listed = client.get("/v1/reminders")
        assert listed.status_code == 200
        assert listed.json()[0]["qstash_message_id"] == "msg_123"
        removed = client.delete(f"/v1/reminders/{record['id']}")
        assert removed.status_code == 200
        assert removed.json()["status"] == "cancelled"

    assert cancelled == ["msg_123"]
    assert '"cancelled"' in store.read_text()
