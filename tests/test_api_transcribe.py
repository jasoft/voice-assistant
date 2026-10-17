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


def test_default_transcribe_does_not_require_legacy_url(monkeypatch, client):
    monkeypatch.delenv("PTT_STT_URL")
    monkeypatch.setattr(api_main, "run_stt", lambda *args: "豆包结果")
    resp = client.post("/v1/transcribe", files={"file": ("a.wav", _wav_bytes(), "audio/wav")})
    assert resp.status_code == 200
    assert resp.json() == {"transcript": "豆包结果"}


def test_fallback_endpoint_skips_doubao(monkeypatch, client):
    monkeypatch.setattr(api_main, "run_stt", lambda *args: pytest.fail("Fallback must skip Doubao"))
    monkeypatch.setattr(api_main, "run_fallback_stt", lambda *args: "原服务结果")
    resp = client.post("/v1/transcribe/fallback", files={"file": ("a.wav", _wav_bytes(), "audio/wav")})
    assert resp.status_code == 200
    assert resp.json() == {"transcript": "原服务结果"}


def test_fallback_endpoint_requires_auth(client):
    resp = client.post("/v1/transcribe/fallback", headers={"Authorization": "Bearer wrong"},
                       files={"file": ("a.wav", _wav_bytes(), "audio/wav")})
    assert resp.status_code == 401


@pytest.mark.parametrize("endpoint,function", [("/v1/transcribe", "run_stt"),
                                             ("/v1/transcribe/fallback", "run_fallback_stt")])
def test_stt_failure_returns_503_and_cleans_upload(monkeypatch, client, endpoint, function):
    paths = []

    def fail(url, token, path):
        paths.append(path)
        raise RuntimeError("provider failed")

    monkeypatch.setattr(api_main, function, fail)
    resp = client.post(endpoint, files={"file": ("a.wav", _wav_bytes(), "audio/wav")})
    assert resp.status_code == 503
    assert len(paths) == 1 and not paths[0].exists()
