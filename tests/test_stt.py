from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from press_to_talk.audio import doubao_stt, stt


def test_doubao_is_default_without_legacy_config(monkeypatch):
    monkeypatch.setattr(stt, "run_doubao_stt", lambda path: "南京明天的天气")
    monkeypatch.setattr(stt, "run_fallback_stt", lambda *args: pytest.fail("Unexpected fallback"))
    assert stt.run_stt("", "", Path("a.wav")) == "南京明天的天气"


@pytest.mark.parametrize("failure", [None, RuntimeError("unavailable"), subprocess.TimeoutExpired("asr", 15)])
def test_empty_or_failed_doubao_uses_legacy_once(monkeypatch, failure):
    def primary(path):
        if failure:
            raise failure
        return ""

    calls = []
    monkeypatch.setattr(stt, "run_doubao_stt", primary)
    monkeypatch.setattr(stt, "run_fallback_stt", lambda *args: calls.append(args) or "原服务结果")
    path = Path("a.wav")
    assert stt.run_stt("https://legacy/v1", "token", path) == "原服务结果"
    assert calls == [("https://legacy/v1", "token", path)]


def test_failed_doubao_without_fallback_is_an_error(monkeypatch):
    def fail(path):
        raise RuntimeError("upstream failure")

    monkeypatch.setattr(stt, "run_doubao_stt", fail)
    with pytest.raises(RuntimeError, match="未配置"):
        stt.run_stt("", "", Path("a.wav"))


def test_worker_is_bounded_and_parses_json(monkeypatch):
    def run(command, **kwargs):
        assert command == [sys.executable, "-m", "press_to_talk.audio.doubao_stt", "a.wav"]
        assert kwargs["timeout"] == 15
        assert kwargs["capture_output"] is True
        if sys.platform == "darwin":
            assert "/opt/homebrew/lib" in kwargs["env"]["DYLD_LIBRARY_PATH"]
        return SimpleNamespace(returncode=0, stdout='{"text":" 南京 "}')

    monkeypatch.setattr(stt.subprocess, "run", run)
    assert stt.run_doubao_stt(Path("a.wav")) == "南京"


@pytest.mark.parametrize("code,body", [(1, ""), (0, "not-json"), (0, '{"text":null}'), (0, "[]")])
def test_worker_rejects_errors_and_invalid_responses(monkeypatch, code, body):
    monkeypatch.setattr(stt.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=code, stdout=body))
    with pytest.raises((TypeError, ValueError, RuntimeError)):
        stt.run_doubao_stt(Path("a.wav"))


def test_fallback_preserves_original_http_contract(monkeypatch, tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"test-audio")

    def post(url, **kwargs):
        assert url == "https://legacy/v1/audio/transcriptions"
        assert kwargs["headers"] == {"Authorization": "Bearer test-token"}
        assert kwargs["files"]["file"][1].read() == b"test-audio"
        assert kwargs["data"] == {"model": "elevenlabs-transcription"}
        assert kwargs["timeout"].connect == 5
        return httpx.Response(200, json={"text": " 原服务结果 "}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert stt.run_fallback_stt("https://legacy/v1/", "test-token", audio) == "原服务结果"


def test_worker_uses_private_persistent_credentials(monkeypatch, tmp_path, capsys):
    credentials = tmp_path / "asr" / "credentials.json"

    class Config:
        def __init__(self, **kwargs):
            assert kwargs == {"credential_path": credentials, "connect_timeout": 5, "recv_timeout": 5}

        def ensure_credentials(self):
            credentials.write_text("{}")

    async def transcribe(path, *, config, realtime):
        assert path == Path("a.wav") and realtime is False
        return "识别成功"

    monkeypatch.setenv("PTT_DOUBAO_CREDENTIAL_PATH", str(credentials))
    monkeypatch.setattr(sys, "argv", ["doubao_stt", "a.wav"])
    monkeypatch.setattr(doubao_stt.os, "umask", lambda value: None)
    monkeypatch.setitem(sys.modules, "doubaoime_asr", SimpleNamespace(ASRConfig=Config, transcribe=transcribe))
    doubao_stt.main()
    assert json.loads(capsys.readouterr().out) == {"text": "识别成功"}
    assert credentials.stat().st_mode & 0o777 == 0o600
