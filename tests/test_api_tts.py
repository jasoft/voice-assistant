"""POST /v1/tts：文本转语音中继的契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from press_to_talk.api import main as api_main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PTT_QUERY_BACKEND", "harness")
    monkeypatch.setenv("PTT_API_KEY", "test-token")
    monkeypatch.setenv("PTT_STT_URL", "http://fake-tts.local/v1")
    monkeypatch.setattr(api_main, "base_config", SimpleNamespace(), raising=False)
    test_client = TestClient(api_main.app)
    test_client.headers.update({"Authorization": "Bearer test-token"})
    return test_client


def test_tts_relays_audio(monkeypatch, client):
    async def fake_relay(text):
        assert text == "你好"
        return b"ID3fakeaudio", "audio/mpeg"

    monkeypatch.setattr(api_main, "_tts_relay", fake_relay)

    resp = client.post("/v1/tts", json={"text": "你好"})

    assert resp.status_code == 200
    assert resp.content == b"ID3fakeaudio"
    assert resp.headers["content-type"].startswith("audio/mpeg")


def test_tts_rejects_blank_text(client):
    resp = client.post("/v1/tts", json={"text": "   "})
    assert resp.status_code == 400


def test_tts_maps_upstream_failure(monkeypatch, client):
    async def fake_relay(text):
        raise RuntimeError("TTS 上游返回 500：boom")

    monkeypatch.setattr(api_main, "_tts_relay", fake_relay)

    resp = client.post("/v1/tts", json={"text": "你好"})

    assert resp.status_code == 502
    assert "boom" in resp.json()["detail"]


def test_tts_requires_backend(monkeypatch, client):
    monkeypatch.delenv("PTT_STT_URL", raising=False)
    monkeypatch.delenv("PTT_TTS_URL", raising=False)

    resp = client.post("/v1/tts", json={"text": "你好"})

    assert resp.status_code == 502
    assert "未配置" in resp.json()["detail"]
