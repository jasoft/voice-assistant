"""POST /v1/transcribe：仅转写端点的契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from press_to_talk.api import main as api_main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PTT_QUERY_BACKEND", "harness")
    monkeypatch.setenv("PTT_API_KEY", "test-token")
    monkeypatch.setenv("PTT_STT_URL", "http://fake-stt.local/v1")
    monkeypatch.setenv("PTT_STT_TOKEN", "fake-token")
    monkeypatch.setattr(api_main, "base_config", SimpleNamespace(), raising=False)
    test_client = TestClient(api_main.app)
    test_client.headers.update({"Authorization": "Bearer test-token"})
    return test_client


def _wav_bytes(seconds: float = 1.0, rate: int = 16000) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def test_transcribe_returns_text(monkeypatch, client):
    def fake_run_stt(url, token, path):
        return " 帮我记一下 明天买牛奶 "

    monkeypatch.setattr(api_main, "run_stt", fake_run_stt)

    resp = client.post(
        "/v1/transcribe", files={"file": ("a.wav", _wav_bytes(), "audio/wav")}
    )

    assert resp.status_code == 200
    assert resp.json()["transcript"] == "帮我记一下 明天买牛奶"


def test_transcribe_rejects_short_recording(client):
    resp = client.post(
        "/v1/transcribe", files={"file": ("a.wav", _wav_bytes(seconds=0.05), "audio/wav")}
    )
    assert resp.status_code == 400


def test_transcribe_rejects_empty_transcript(monkeypatch, client):
    def fake_run_stt(url, token, path):
        return "   "

    monkeypatch.setattr(api_main, "run_stt", fake_run_stt)

    resp = client.post(
        "/v1/transcribe", files={"file": ("a.wav", _wav_bytes(), "audio/wav")}
    )
    assert resp.status_code == 422
