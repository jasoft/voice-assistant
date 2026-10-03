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
    monkeypatch.setenv("GOOGLE_AI_STUDIO_KEY", "fake-key")
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
        raise RuntimeError("Gemini TTS 上游返回 500：boom")
        yield b""  # pragma: no cover

    monkeypatch.setattr(api_main, "_gemini_tts_stream", fake_stream)

    resp = client.post("/v1/tts", json={"text": "你好"})

    assert resp.status_code == 502
    assert "boom" in resp.json()["detail"]


def test_tts_requires_key(monkeypatch, client):
    monkeypatch.delenv("GOOGLE_AI_STUDIO_KEY", raising=False)

    resp = client.post("/v1/tts", json={"text": "你好"})

    assert resp.status_code == 502
    assert "GOOGLE_AI_STUDIO_KEY" in resp.json()["detail"]


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
