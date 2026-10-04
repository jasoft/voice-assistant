"""POST /v1/tts：Gemini 流式 TTS 中继的契约测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from press_to_talk.api import main as api_main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PTT_QUERY_BACKEND", "harness")
    monkeypatch.setenv("PTT_API_KEY", "test-token")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-dashscope-key")
    monkeypatch.delenv("GOOGLE_AI_STUDIO_KEY", raising=False)
    monkeypatch.setattr(api_main, "base_config", SimpleNamespace(), raising=False)
    test_client = TestClient(api_main.app)
    test_client.headers.update({"Authorization": "Bearer test-token"})
    return test_client


def test_tts_streams_pcm(monkeypatch, client):
    async def fake_stream(text):
        assert text == "你好"
        yield b"\x01\x02\x03\x04"
        yield b"\x05\x06"

    monkeypatch.setattr(api_main, "_gemini_tts_stream", fake_stream)

    resp = client.post("/v1/tts", json={"text": "你好"})

    assert resp.status_code == 200
    assert resp.content == b"\x01\x02\x03\x04\x05\x06"
    assert resp.headers["x-audio-format"].startswith("pcm;")


def test_tts_rejects_blank_text(client):
    resp = client.post("/v1/tts", json={"text": "   "})
    assert resp.status_code == 400


def test_tts_maps_upstream_failure(monkeypatch, client):
    async def fake_stream(text):
        raise RuntimeError("Qwen TTS 上游返回 500：boom")
        yield b""  # pragma: no cover

    monkeypatch.setattr(api_main, "_gemini_tts_stream", fake_stream)

    resp = client.post("/v1/tts", json={"text": "你好"})

    assert resp.status_code == 502
    assert "boom" in resp.json()["detail"]


def test_tts_requires_key(monkeypatch, client):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_AI_STUDIO_KEY", raising=False)

    resp = client.post("/v1/tts", json={"text": "你好"})

    assert resp.status_code == 502
    assert "DASHSCOPE_API_KEY" in resp.json()["detail"]


def test_gemini_stream_parses_sse(monkeypatch):
    """_gemini_tts_stream 应解析 SSE inlineData 并按 2 字节对齐。"""

    class FakeResponse:
        status_code = 200

        async def aread(self):
            return b""

        async def aiter_lines(self):
            payload = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"inlineData": {"data": "AQIDBA=="}}  # 4 字节
                            ]
                        }
                    }
                ]
            }
            yield "data: " + json.dumps(payload)
            payload2 = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"inlineData": {"data": "BQYH"}}  # 3 字节，奇数
                            ]
                        }
                    }
                ]
            }
            yield "data: " + json.dumps(payload2)

    class FakeClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, json=None):
            import contextlib

            @contextlib.asynccontextmanager
            async def cm():
                yield FakeResponse()

            return cm()

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "AsyncClient", lambda timeout=None: FakeClient())
    monkeypatch.setenv("GOOGLE_AI_STUDIO_KEY", "fake-key")

    async def run() -> list[bytes]:
        chunks = []
        async for piece in api_main._gemini_tts_stream("测试"):
            chunks.append(piece)
        return chunks

    import asyncio

    chunks = asyncio.run(run())
    # 第一块 4 字节直接 yield；第二块 3 字节（奇数）→ 前 2 字节 yield，尾字节 <2 丢弃
    assert chunks == [b"\x01\x02\x03\x04", b"\x05\x06"]


def test_qwen_stream_parses_sse(monkeypatch):
    """_qwen_tts_stream 应解析 SSE base64 数据块并按 2 字节对齐。"""

    class FakeResponse:
        status_code = 200

        async def aread(self):
            return b""

        async def aiter_lines(self):
            # chunk 1: 4 字节 base64 (AQIDBA==)
            chunk1 = {
                "output": {"audio": {"data": "AQIDBA=="}},
                "finish_reason": "null",
            }
            yield "data: " + json.dumps(chunk1)

            # chunk 2: 3 字节 base64 (BQYH) -> 奇数
            chunk2 = {
                "output": {"audio": {"data": "BQYH"}},
                "finish_reason": "null",
            }
            yield "data: " + json.dumps(chunk2)

            # chunk 3: 结束帧，无音频
            chunk3 = {
                "output": {"audio": {"data": "", "url": "http://example.com/audio.wav"}},
                "finish_reason": "stop",
            }
            yield "data: " + json.dumps(chunk3)

    class FakeClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, headers=None, json=None):
            import contextlib

            assert json["model"] == "qwen-audio-3.1-tts-flash"
            assert "SpeechSynthesizer" in url
            assert json["input"]["voice"] == "yuxiaoyun_v3.1"
            assert json["input"]["format"] == "pcm"

            @contextlib.asynccontextmanager
            async def cm():
                yield FakeResponse()

            return cm()

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "AsyncClient", lambda timeout=None: FakeClient())
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("PTT_TTS_MODEL", "qwen-audio-3.1-tts-flash")
    monkeypatch.setenv("PTT_TTS_VOICE", "yuxiaoyun_v3.1")

    async def run() -> list[bytes]:
        chunks = []
        async for piece in api_main._qwen_tts_stream("测试"):
            chunks.append(piece)
        return chunks

    import asyncio

    chunks = asyncio.run(run())
    assert chunks == [b"\x01\x02\x03\x04", b"\x05\x06"]


def test_qwen_stream_strips_wav_header(monkeypatch):
    """_qwen_tts_stream 遇到含 RIFF/WAV 头的数据时，应自动剥离头部仅输出 PCM 数据。"""
    import base64

    # 构造含 44 字节标准 WAV 头和 4 字节 PCM 数据的数据包
    wav_header = (
        b"RIFF\x28\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\xc0\x5d\x00\x00\x80\xbb\x00\x00\x02\x00\x10\x00data\x04\x00\x00\x00"
    )
    pcm_payload = b"\xaa\xbb\xcc\xdd"
    full_wav = wav_header + pcm_payload
    b64_data = base64.b64encode(full_wav).decode("ascii")

    class FakeResponse:
        status_code = 200

        async def aread(self):
            return b""

        async def aiter_lines(self):
            chunk = {
                "output": {"audio": {"data": b64_data}},
                "finish_reason": "null",
            }
            yield "data: " + json.dumps(chunk)
            # 第二块：裸 PCM
            chunk2 = {
                "output": {"audio": {"data": base64.b64encode(b"\x11\x22").decode("ascii")}},
                "finish_reason": "stop",
            }
            yield "data: " + json.dumps(chunk2)

    class FakeClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, headers=None, json=None):
            import contextlib

            @contextlib.asynccontextmanager
            async def cm():
                yield FakeResponse()

            return cm()

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "AsyncClient", lambda timeout=None: FakeClient())
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    async def run() -> list[bytes]:
        chunks = []
        async for piece in api_main._qwen_tts_stream("测试"):
            chunks.append(piece)
        return chunks

    import asyncio

    chunks = asyncio.run(run())
    assert chunks == [b"\xaa\xbb\xcc\xdd", b"\x11\x22"]


