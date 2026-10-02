"""POST /v1/ask-audio：服务端 STT 转写 + fast-path/Harness 链路的契约测试。"""

from __future__ import annotations

import io
import wave
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from press_to_talk.api import main as api_main


def _wav_bytes(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class _FakeHarnessClient:
    def __init__(self, reply: str):
        self.reply = reply
        self.closed = False

    async def query(self, text, *, photo=None, timeout_seconds=None):
        return {"reply": self.reply, "query": text, "debug_info": {"session_id": "fake"}}

    async def close(self):
        self.closed = True


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PTT_QUERY_BACKEND", "harness")
    monkeypatch.setenv("PTT_API_KEY", "test-token")
    monkeypatch.setenv("PTT_STT_URL", "http://fake-stt.local/v1")
    monkeypatch.setenv("PTT_STT_TOKEN", "fake-token")
    monkeypatch.setattr(api_main, "base_config", SimpleNamespace(), raising=False)
    monkeypatch.setattr(api_main, "_persist_harness_history", lambda **kwargs: None)
    test_client = TestClient(api_main.app)
    test_client.headers.update({"Authorization": "Bearer test-token"})
    return test_client


def test_ask_audio_fast_path(monkeypatch, client):
    def fake_run_stt(url, token, path):
        return " 帮我记一下 明天买牛奶 "

    async def fake_fast(query, *, stream_callback=None, selected_text=None):
        assert query == "帮我记一下 明天买牛奶"
        return {
            "reply": "✅ 已记入 Memos：明天买牛奶",
            "action": "speak",
            "memories": [],
            "query": query,
            "debug_info": {"typesafe_s": 0.1},
        }

    monkeypatch.setattr(api_main, "run_stt", fake_run_stt)
    monkeypatch.setattr(api_main, "try_fast_memory_chat", fake_fast)

    resp = client.post("/v1/ask-audio", files={"file": ("a.wav", _wav_bytes(), "audio/wav")})

    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"] == "帮我记一下 明天买牛奶"
    assert body["reply"].startswith("✅")
    assert body["action"] == "speak"


def test_ask_audio_falls_back_to_harness(monkeypatch, client):
    fake = _FakeHarnessClient("晴天，适合出门")

    async def fake_fast(query, *, stream_callback=None, selected_text=None):
        return None

    def fake_run_stt(url, token, path):
        return "今天天气怎么样"

    monkeypatch.setattr(api_main, "try_fast_memory_chat", fake_fast)
    monkeypatch.setattr(api_main, "run_stt", fake_run_stt)
    monkeypatch.setattr(api_main, "_chat_harness_client_for", lambda user_id: fake)

    resp = client.post("/v1/ask-audio", files={"file": ("a.wav", _wav_bytes(), "audio/wav")})

    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"] == "今天天气怎么样"
    assert body["reply"] == "晴天，适合出门"
    assert fake.closed is True


def test_ask_audio_rejects_short_recording(client):
    resp = client.post(
        "/v1/ask-audio", files={"file": ("a.wav", _wav_bytes(seconds=0.05), "audio/wav")}
    )
    assert resp.status_code == 400
    assert "录音太短" in resp.json()["detail"]


def test_ask_audio_rejects_empty_upload(client):
    resp = client.post("/v1/ask-audio", files={"file": ("a.wav", b"", "audio/wav")})
    assert resp.status_code == 400
    assert "音频内容为空" in resp.json()["detail"]


def test_ask_audio_rejects_empty_transcript(monkeypatch, client):
    def fake_run_stt(url, token, path):
        return "   "

    monkeypatch.setattr(api_main, "run_stt", fake_run_stt)

    resp = client.post("/v1/ask-audio", files={"file": ("a.wav", _wav_bytes(), "audio/wav")})

    assert resp.status_code == 422
    assert "未能识别" in resp.json()["detail"]
