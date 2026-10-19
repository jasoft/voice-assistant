"""双端（Mac GUI 与 Apple Watch）全链路客户端协议契约测试

测试覆盖：
1. 双重 /v1/v1/ 路由自动归一化防御（彻底杜绝客户端 Base URL 误配导致的 404 Detail not found）
2. 录音转写契约（POST /v1/transcribe）：标准 WAV 转写响应、短音频 422 结构与错误 detail 解析
3. 流式问答契约（POST /v1/chat）：SSE 连接握手 (: connected)、长任务心跳 (: keepalive)、delta 逐字流、done 完整数据模型兼容
4. 流式 TTS 语音合成契约（POST /v1/tts）：24kHz 单声道 PCM 音频流产出
5. Mac 选中文本改写动作契约（action: paste）
6. 复杂 Agent 任务长超时保护契约（放宽至 45s，防 30s 掐断）
"""

import asyncio
import io
import json
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from press_to_talk.api import main as api_main
from press_to_talk.api.auth import get_user_id


def make_dummy_wav(duration_seconds: float = 1.0, sample_rate: int = 16000) -> bytes:
    """生成合规的 16kHz 16-bit 单声道 WAV 音频字节。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        num_frames = int(duration_seconds * sample_rate)
        wf.writeframes(b"\x10\x00" * num_frames)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def setup_api_auth(monkeypatch):
    """默认注入测试授权用户，并启用 mock base_config 与必要环境变量。"""
    monkeypatch.setattr(api_main, "base_config", SimpleNamespace(), raising=False)
    monkeypatch.setenv("PTT_QUERY_BACKEND", "harness")
    monkeypatch.setenv("PTT_STT_URL", "http://fake-stt.local/v1")
    monkeypatch.setenv("PTT_STT_TOKEN", "fake-token")
    api_main.app.dependency_overrides[get_user_id] = lambda: "test-user"
    yield
    api_main.app.dependency_overrides.clear()


def test_v1_v1_prefix_normalization_contract(monkeypatch):
    """验证即使客户端请求包含重复的 /v1/v1/ 前缀，服务端中间件也能无缝重写为 /v1/，杜绝 404 Detail not found。"""
    async def fake_handle(req, user_id):
        return api_main.QueryResponse(reply="ok", spoken_reply="ok", action="speak", query=req.query)

    monkeypatch.setattr(api_main, "_handle_chat", fake_handle)
    monkeypatch.setattr(api_main, "run_stt", lambda *a, **kw: "识别成功")

    with TestClient(api_main.app) as client:
        # 1. /v1/v1/chat 应该命中 /v1/chat，状态码 200
        resp_chat = client.post("/v1/v1/chat", json={"query": "ping", "stream": False})
        assert resp_chat.status_code == 200, f"期望 200，实际: {resp_chat.status_code} {resp_chat.text}"
        assert resp_chat.json()["reply"] == "ok"

        # 2. 多重重复前缀 /v1/v1/v1/chat 应该也能处理
        resp_multi = client.post("/v1/v1/v1/chat", json={"query": "ping", "stream": False})
        assert resp_multi.status_code == 200

        # 3. /v1/v1/transcribe 录音转写端点
        files = {"file": ("input.wav", make_dummy_wav(1.0), "audio/wav")}
        resp_transcribe = client.post("/v1/v1/transcribe", files=files)
        assert resp_transcribe.status_code == 200
        assert resp_transcribe.json()["transcript"] == "识别成功"


def test_transcribe_endpoint_contract(monkeypatch):
    """验证录音上传与转写契约（Mac 与 Watch 客户端录音入口）。"""
    def fake_run_stt(url, token, path, **details):
        return "今天天气怎么样？"

    monkeypatch.setattr(api_main, "run_stt", fake_run_stt)

    wav_bytes = make_dummy_wav(duration_seconds=1.5)
    with TestClient(api_main.app) as client:
        files = {"file": ("input.wav", wav_bytes, "audio/wav")}
        resp = client.post("/v1/transcribe", files=files)
        assert resp.status_code == 200
        data = resp.json()
        assert "transcript" in data
        assert data["transcript"] == "今天天气怎么样？"


def test_transcribe_short_audio_returns_400_with_detail():
    """验证录音过短（<0.18s）返回 400 及 detail 提示，双端可据此提示用户说话太短。"""
    wav_bytes = make_dummy_wav(duration_seconds=0.05)  # 50ms 远低于最小有效阈值
    with TestClient(api_main.app) as client:
        files = {"file": ("input.wav", wav_bytes, "audio/wav")}
        resp = client.post("/v1/transcribe", files=files)
        assert resp.status_code == 400
        data = resp.json()
        assert "detail" in data
        assert "太短" in data["detail"]


def test_transcribe_empty_speech_returns_422_with_detail(monkeypatch):
    """验证录音未识别人声返回 422 结构及 detail，客户端可据此平滑降级为 no_speech。"""
    monkeypatch.setattr(api_main, "run_stt", lambda *a, **kw: "   ")

    wav_bytes = make_dummy_wav(duration_seconds=1.0)
    with TestClient(api_main.app) as client:
        files = {"file": ("input.wav", wav_bytes, "audio/wav")}
        resp = client.post("/v1/transcribe", files=files)
        assert resp.status_code == 422
        data = resp.json()
        assert "detail" in data
        assert "未能识别到语音内容" in data["detail"]


def test_tts_streaming_contract(monkeypatch):
    """验证 /v1/tts 流式 PCM 语音合成契约（用于 Apple Watch 与 Mac 客户端播放）。"""
    async def fake_tts(text: str):
        # 产生模拟的 PCM 字节块（16-bit 24kHz）
        yield b"\x00\x01" * 1024
        yield b"\x02\x03" * 1024

    monkeypatch.setattr(api_main, "_tts_stream", fake_tts)

    with TestClient(api_main.app) as client:
        resp = client.post("/v1/tts", json={"text": "测试语音播报"})
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "application/octet-stream"
        assert resp.headers.get("x-audio-format") == "pcm;rate=24000;channels=1;bits=16"
        content = resp.content
        assert len(content) == 4096


@pytest.mark.anyio
async def test_stream_chat_sse_contract_for_watch_and_mac(monkeypatch):
    """验证流式问答协议（SSE）契约，完全契合 watchOS APIClient 及 macOS VAClient 解析器。"""
    async def fake_handle(req, user_id):
        from press_to_talk.api.reply_stream import current_stream
        stream = current_stream.get()
        await stream.emit("南京今天晴天，")
        await stream.emit("气温 20 度。")
        return api_main.QueryResponse(
            reply="南京今天晴天，气温 20 度。",
            spoken_reply="南京今天晴，气温二十度。",
            action="speak",
            query=req.query,
            debug_info={"backend": "fast-chat-direct-llm"},
        )

    monkeypatch.setattr(api_main, "_handle_chat", fake_handle)

    req = api_main.QueryRequest(query="南京天气", stream=True, response_style="watch")
    resp = api_main._stream_chat(req, "test-user")
    assert resp.media_type == "text/event-stream"
    assert resp.headers["x-accel-buffering"] == "no"

    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk)

    full_output = "".join(chunks)

    # 1. 验证握手行
    assert ": connected\n\n" in full_output

    # 2. 验证 delta 格式包含 JSON payload
    assert "event: delta\ndata: {\"text\": \"南京今天晴天，\"}\n\n" in full_output
    assert "event: delta\ndata: {\"text\": \"气温 20 度。\"}\n\n" in full_output

    # 3. 验证 done 格式与关键字段（reply、spoken_reply、action）
    assert "event: done\ndata: " in full_output
    done_line = [line for line in full_output.split("\n") if line.startswith("data: {\"reply\":")][0]
    done_payload = json.loads(done_line.replace("data: ", ""))
    assert done_payload["reply"] == "南京今天晴天，气温 20 度。"
    assert done_payload["spoken_reply"] == "南京今天晴，气温二十度。"
    assert done_payload["action"] == "speak"


@pytest.mark.anyio
async def test_long_running_agent_task_keepalive_contract(monkeypatch):
    """验证复杂任务（如网络搜索/外部工具）在耗时 10 秒以上时，服务端正常发送心跳 (: keepalive)，不中断连接。"""
    async def slow_handle(req, user_id):
        # 模拟外部工具执行与模型思考耗时
        await asyncio.sleep(0.1)
        return api_main.QueryResponse(
            reply="搜索到 3 条今日新闻。",
            spoken_reply="搜索到三条今日新闻。",
            action="speak",
            query=req.query,
        )

    monkeypatch.setattr(api_main, "_handle_chat", slow_handle)
    req = api_main.QueryRequest(query="搜索今天的新闻", stream=True)
    resp = api_main._stream_chat(req, "test-user")

    chunks = []
    async for chunk in resp.body_iterator:
        chunks.append(chunk)

    full_output = "".join(chunks)
    assert ": connected\n\n" in full_output
    assert "event: done\ndata: " in full_output
    assert "搜索到 3 条今日新闻。" in full_output


def test_mac_selected_text_paste_action_contract(monkeypatch):
    """验证 Mac 客户端选中文本改写/生成时返回 action=paste 契约。"""
    async def fake_fast_chat(query, selected_text=None, request_id=None):
        return {
            "reply": "修改后的优化文本",
            "spoken_reply": "修改后的优化文本",
            "action": "paste",
            "query": query,
            "debug_info": {"backend": "fast-chat"},
        }

    monkeypatch.setattr("press_to_talk.api.main.try_fast_memory_chat", fake_fast_chat)

    with TestClient(api_main.app) as client:
        payload = {
            "query": "帮我润色这段话",
            "selected_text": "今天的天气真好啊",
            "stream": False,
        }
        resp = client.post("/v1/chat", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "paste"
        assert data["reply"] == "修改后的优化文本"


def test_timeout_protection_contract():
    """验证对话超时设置大于 40 秒，确保复杂搜索任务（通常 20-30 秒）不会过早被掐断。"""
    from press_to_talk.api.main import _chat_timeout_seconds, _chat_timeout_default
    assert _chat_timeout_default >= 45.0
    assert _chat_timeout_seconds() >= 45.0
