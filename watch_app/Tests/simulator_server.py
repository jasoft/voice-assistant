"""Local deterministic simulator fixture using the real API and OpenAI SSE parser.

uv run python watch_app/Tests/simulator_server.py
Only external decision/LLM/TTS/history boundaries are replaced; no personal data writes.
PCM is a test tone, not a claim of real TTS quality. Use a real API for voice validation.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import asyncio
import json
import math
import os
import struct
import time
from types import SimpleNamespace

import uvicorn
from fastapi import Request
from fastapi.responses import StreamingResponse

os.environ.update(PTT_QUERY_BACKEND="harness", PTT_API_KEY="watch-simulator-test", OPENAI_BASE_URL="http://localhost:10039/simulator-upstream", OPENAI_API_KEY="fixture", PTT_MODEL="fixture", PTT_CHAT_TIMEOUT_SECONDS="30")
from press_to_talk.api import main, fast_chat

main.base_config = SimpleNamespace()
main._persist_harness_history = lambda **kwargs: None
fast_chat.ask_intent_and_delivery = lambda *args: {"intent": "chat", "delivery": "speak"}

trace = Path("/tmp/va-watch-simulator-events.jsonl")

def record(event, **data):
    with trace.open("a") as file:
        file.write(json.dumps({"time": time.time(), "event": event, **data}, ensure_ascii=False) + "\n")

@main.app.post("/simulator-upstream/chat/completions")
async def completion(req: Request):
    payload = await req.json()
    text = str(payload["messages"])
    pieces = ["先看结论：", "手表界面已经更清晰。\n\n", "- 回答优先显示。\n", "- 转动表冠阅读全文。\n", "- 点按停止可以打断语音。\n\n", "文字还在生成时，语音就能开始播放。"]
    if "长回答测试" in text:
        pieces += [f"\n第{i}条：正文完整保留，滚动后仍能看到操作按钮。" for i in range(1, 9)]
    if "等待测试" in text: await asyncio.sleep(12)
    async def chunks():
        try:
            for i, piece in enumerate(pieces):
                await asyncio.sleep(0.8)
                record("upstream_delta", text=piece)
                value = {"id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": "fixture", "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                yield "data: " + json.dumps(value, ensure_ascii=False) + "\n\n"
                if "中断测试" in text and i == 1:
                    # OpenAI parser sees a truncated completion with explicit failure.
                    value["choices"][0] = {"index": 0, "delta": {}, "finish_reason": "length"}
                    yield "data: " + json.dumps(value) + "\n\n"
                    return
            record("upstream_done")
            yield 'data: {"id":"fixture","object":"chat.completion.chunk","created":0,"model":"fixture","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            yield "data: [DONE]\n\n"
        finally:
            record("upstream_closed")
    return StreamingResponse(chunks(), media_type="text/event-stream")

async def tone(text):
    record("tts_start", text=text)
    if "播放失败测试" in text: raise RuntimeError("fixture TTS failure")
    samples = [int(2500 * math.sin(i * 2 * math.pi * 440 / 24000)) for i in range(12000)]
    pcm = struct.pack("<" + "h" * len(samples), *samples)
    for i in range(0, len(pcm), 4800):
        yield pcm[i:i + 4800]
        await asyncio.sleep(0.05)
    record("tts_done")
main._gemini_tts_stream = tone

if __name__ == "__main__":
    uvicorn.run(main.app, host="127.0.0.1", port=10039, log_level="warning")
