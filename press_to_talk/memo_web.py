from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .harness import DeepSeekHarnessClient, HarnessError
from .utils.env import load_env_files
from .utils.logging import log


WEB_ROOT = Path(__file__).resolve().parent.parent / "memo_web"


class MemoQueryRequest(BaseModel):
    """A single instruction sent to the configured memo agent."""

    instruction: str = Field(..., min_length=1, max_length=10_000)


class ChatApiRequest(BaseModel):
    """Query payload compatible with /v1/chat endpoint."""

    query: str | None = Field(default=None)
    instruction: str | None = Field(default=None)
    selected_text: str | None = Field(default=None)


class MemoQueryResponse(BaseModel):
    """The final response returned by the memo agent."""

    reply: str
    agent: str
    session_id: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env_files()
    client = DeepSeekHarnessClient.from_env()
    app.state.harness_client = client
    try:
        yield
    finally:
        await client.close()
        app.state.harness_client = None


app = FastAPI(
    title="Memo Web",
    description="A mobile-friendly web shell for the DeepSeek Harness memo agent.",
    lifespan=lifespan,
)


@app.middleware("http")
async def add_cache_control_headers(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".css", ".js", ".json")):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def _harness_client() -> DeepSeekHarnessClient:
    client = getattr(app.state, "harness_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Memo Agent 尚未准备好")
    return client


from .version import get_version


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a lightweight liveness response for LAN checks."""

    return {"status": "ok", "version": get_version()}


@app.get("/api/version")
async def version() -> dict[str, str]:
    """Return the current service version."""

    return {"version": get_version()}


def _clean_reply(raw_reply: str) -> str:
    """清除回复中对人类无意义的 memo ID。"""
    import re

    cleaned_reply = re.sub(
        r'[\(（\[【]\s*(?:ID[:：]\s*)?(?:memos\/[A-Za-z0-9_-]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*[\)）\]】]',
        '',
        raw_reply,
        flags=re.IGNORECASE,
    )
    cleaned_reply = re.sub(
        r'(?:ID|编号|id)[:：]\s*(?:memos\/[A-Za-z0-9_-]+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b',
        '',
        cleaned_reply,
        flags=re.IGNORECASE,
    )
    cleaned_reply = re.sub(r'\bmemos\/[A-Za-z0-9_-]+\b', '', cleaned_reply)
    cleaned_reply = re.sub(r'[\(（]\s*[\)）]', '', cleaned_reply)
    lines = [re.sub(r'[ \t]+$', '', line) for line in cleaned_reply.splitlines()]
    return '\n'.join(lines).strip() or raw_reply


async def _execute_chat_pipeline(query_text: str) -> dict[str, Any]:
    """统一执行与 /v1/chat 相同的智能意图与分流链路。"""
    # 1. 尝试 Fast path（TypeSafe / clef 意图二分 + Memos 直写/CEL直查 + Fast LLM 直出）
    try:
        from .api.fast_chat import try_fast_memory_chat

        fast_result = await try_fast_memory_chat(query_text)
        if fast_result is not None:
            raw_reply = str(fast_result.get("reply", ""))
            return {
                "reply": _clean_reply(raw_reply),
                "agent": "fast-chat",
                "session_id": None,
                "debug_info": fast_result.get("debug_info"),
                "action": fast_result.get("action", "speak"),
            }
        log("Memo Web fast-path 未命中（fast_result=None），回退 chat-fast Agent", level="info")
    except Exception as exc:
        log(f"Memo Web fast-path 异常，回退 chat-fast Agent: {exc}", level="warn")

    # 2. 回退到 chat-fast Harness Agent（具备联网搜索 FreeSerp 与记忆综合能力）
    chat_preset = os.environ.get("PTT_CHAT_HARNESS_AGENT_PRESET", "chat-fast")
    timeout = float(os.environ.get("PTT_CHAT_TIMEOUT_SECONDS", "30.0"))
    existing_client = getattr(app.state, "harness_client", None)
    should_close = False
    if existing_client is not None:
        harness_client = existing_client
    else:
        harness_client = DeepSeekHarnessClient.from_env(
            agent_preset=chat_preset,
            timeout_seconds=timeout,
            poll_interval_seconds=float(os.environ.get("PTT_CHAT_POLL_INTERVAL_SECONDS", "0.1")),
        )
        should_close = True
    try:
        log(f"Memo Web 走 chat-fast Harness Agent ({chat_preset}): {query_text[:80]}", level="info")
        result = await harness_client.query(query_text)
    except HarnessError as exc:
        log(f"Memo Web Harness query failed: {exc}", level="error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        log(f"Memo Web Harness query setup failed: {exc}", level="error")
        raise HTTPException(status_code=502, detail="无法连接 DeepSeek Harness") from exc
    finally:
        if should_close:
            await harness_client.close()

    raw_reply = str(result.get("reply", ""))
    debug_info = result.get("debug_info")
    session_id = debug_info.get("session_id") if isinstance(debug_info, dict) else None
    raw_agent = getattr(harness_client, "agent_preset", chat_preset)
    agent_name = str(raw_agent) if isinstance(raw_agent, str) else chat_preset

    return {
        "reply": _clean_reply(raw_reply),
        "agent": agent_name,
        "session_id": str(session_id) if session_id else None,
        "debug_info": debug_info,
        "action": "speak",
    }


@app.post("/v1/chat")
@app.post("/chat", include_in_schema=False)
async def chat(request: ChatApiRequest) -> dict[str, Any]:
    """跟 debug 页面完全一致的 /v1/chat 接口，自动判断意图并给出结果。"""
    query_text = (request.query or request.instruction or "").strip()
    if not query_text:
        raise HTTPException(status_code=422, detail="查询内容不能为空")
    res = await _execute_chat_pipeline(query_text)
    return {
        "reply": res["reply"],
        "action": res.get("action", "speak"),
        "query": query_text,
        "debug_info": res.get("debug_info") or {"backend": res["agent"]},
    }


@app.post("/api/query", response_model=MemoQueryResponse)
async def query(request: MemoQueryRequest) -> MemoQueryResponse:
    """向后兼容 /api/query，底层完全走 /v1/chat 自动意图与分流链路。"""
    instruction = request.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="指令不能为空")
    log(f"Memo Web 收到指令: {instruction[:80]}", level="info")

    res = await _execute_chat_pipeline(instruction)
    return MemoQueryResponse(
        reply=res["reply"],
        agent=res["agent"],
        session_id=res["session_id"],
    )


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the mobile web shell."""

    return FileResponse(WEB_ROOT / "index.html")


app.mount("/", StaticFiles(directory=WEB_ROOT), name="memo-web")


def run_server() -> None:
    """Run the memo web shell on a LAN-accessible HTTP listener."""

    import uvicorn

    parser = argparse.ArgumentParser(description="Run the Memo mobile web shell.")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to.")
    parser.add_argument("--port", type=int, default=10032, help="Port to bind to.")
    args = parser.parse_args()
    uvicorn.run("press_to_talk.memo_web:app", host=args.host, port=args.port)


if __name__ == "__main__":
    run_server()
