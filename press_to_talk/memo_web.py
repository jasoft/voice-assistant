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


def _harness_client() -> DeepSeekHarnessClient:
    client = getattr(app.state, "harness_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Memo Agent 尚未准备好")
    return client


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a lightweight liveness response for LAN checks."""

    return {"status": "ok"}


@app.post("/api/query", response_model=MemoQueryResponse)
async def query(request: MemoQueryRequest) -> MemoQueryResponse:
    """Send one instruction to memo and wait for its final assistant message."""

    instruction = request.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="指令不能为空")

    client = _harness_client()
    try:
        result = await client.query(instruction)
    except HarnessError as exc:
        log(f"Memo Web Harness query failed: {exc}", level="error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        log(f"Memo Web Harness query setup failed: {exc}", level="error")
        raise HTTPException(status_code=502, detail="无法连接 DeepSeek Harness") from exc

    debug_info = result.get("debug_info")
    session_id = debug_info.get("session_id") if isinstance(debug_info, dict) else None
    return MemoQueryResponse(
        reply=str(result.get("reply", "")),
        agent=os.environ.get("PTT_HARNESS_AGENT_PRESET", "memo-mem0"),
        session_id=str(session_id) if session_id else None,
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
