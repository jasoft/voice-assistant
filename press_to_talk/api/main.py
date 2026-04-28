from __future__ import annotations
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import os
import base64
import uuid
from datetime import datetime
import dataclasses
from contextlib import asynccontextmanager

from .auth import get_user_id
from ..models.config import Config, parse_args
from ..execution import execute_transcript_async
from ..storage.models import SessionHistory, RememberEntry, db
from ..storage.service import ensure_storage_database, load_storage_config
from ..utils.logging import log, log_multiline
from ..utils.photo import get_photo_url

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import json

def mask_auth_header(auth_str: str) -> str:
    """Mask Authorization header for security, showing only first 6 and last 4 characters."""
    if not auth_str or len(auth_str) < 10:
        return "***"
    return f"{auth_str[:6]}...{auth_str[-4:]}"

# Global base config to be loaded once at startup
base_config: Optional[Config] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化文件日志
    from ..utils.logging import init_session_log, log, close_session_log
    from pathlib import Path
    log_path = init_session_log(Path("logs"), session_id="api-server")
    log(f"API Server started. Detailed logs at: {log_path}", level="info")

    # Initialize database on startup
    global base_config
    storage_cfg = load_storage_config()
    ensure_storage_database(storage_cfg)
    
    # Load base config once
    try:
        # Web API 使用进程环境和 .env 文件
        base_config = parse_args(["--user-id", "api-server", "--no-tts"], load_env=True)
    except SystemExit:
        # If parse_args failed, we might be missing critical env vars
        # In a real API we might want to log this or handle more gracefully
        base_config = None
        
    yield
    # Cleanup on shutdown
    log("API Server shutting down.", level="info")
    close_session_log()
    
    if not db.is_closed():
        db.close()

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Only log /v1 requests
        if not request.url.path.startswith("/v1"):
            return await call_next(request)

        # Read Body
        body = await request.body()
        
        # Prepare log content: Mask Authorization header
        headers = dict(request.headers)
        if "authorization" in headers:
            headers["authorization"] = mask_auth_header(headers["authorization"])
        
        # Prepare log content: Truncate Body
        try:
            body_str = body.decode("utf-8", errors="replace")
        except Exception:
            body_str = "[binary data]"
            
        if len(body_str) > 1000:
            body_str = body_str[:1000] + "... [truncated]"

        log_content = [
            f"Method: {request.method}",
            f"URL: {request.url}",
            f"Client: {request.client.host if request.client else 'unknown'}",
            f"Headers: {json.dumps(headers, indent=2)}",
            f"Body: {body_str}"
        ]
        log_multiline("API Request Incoming", "\n".join(log_content), level="info")

        # Re-wrap body for subsequent route handlers
        async def receive():
            return {"type": "http.request", "body": body}

        request._receive = receive
        
        response = await call_next(request)
        return response

app = FastAPI(title="Press-to-Talk API", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory="data/photos"), name="assets")
app.add_middleware(LoggingMiddleware)

from enum import Enum

class ExecutionMode(str, Enum):
    MEMORY_CHAT = "memory-chat"
    DATABASE = "database"
    HERMES = "hermes"
    INTENT = "intent"

class PhotoAttachment(BaseModel):
    type: str = Field(..., description="图片类型: 'url' 或 'base64'")
    url: Optional[str] = Field(None, description="当 type 为 'url' 时必填")
    data: Optional[str] = Field(None, description="当 type 为 'base64' 时必填 (Base64 数据)")
    mime: Optional[str] = Field(None, description="可选，图片的 MIME 类型")

class QueryRequest(BaseModel):
    query: str = Field(..., description="用户输入的自然语言查询语句。例如：'最近三天的记录'、'护照在哪？'。")
    mode: Optional[ExecutionMode] = Field(
        default=ExecutionMode.MEMORY_CHAT, 
        description=(
            "执行模式。决定了系统如何处理该查询：\n"
            "- `memory-chat` (默认): 全能模式。先检索相关记忆，再结合上下文进行对话。\n"
            "- `database`: 纯工具模式。只执行确定的数据库增删改查（如记录或精确搜索），不进行发散聊天。\n"
            "- `hermes`: 强制调用外部 Hermes 聊天引擎。\n"
            "- `intent`: `database` 模式的别名。"
        )
    )
    photo: Optional[PhotoAttachment] = Field(None, description="图片附件节点")

    class Config:
        json_schema_extra = {
            "example": {
                "query": "最近三天的记录",
                "mode": "memory-chat",
                "photo": {
                    "type": "base64",
                    "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==",
                    "mime": "image/png"
                }
            }
        }

class HistoryItem(BaseModel):
    session_id: str
    transcript: str
    reply: str
    created_at: str

class MemoryItem(BaseModel):
    id: str
    memory: str
    created_at: str
    photo_path: Optional[str] = None
    photo_url: Optional[str] = None # 新增
    score: float = Field(0.0, description="搜索匹配分数 (0.0 - 1.0)") # 新增

class QueryResponse(BaseModel):
    reply: str
    memories: List[MemoryItem] = Field(default_factory=list, description="所选记忆的完整数据")
    images: List[str] = Field(default_factory=list, description="相关图片的 URL 列表，最多 3 个")
    query: Optional[str] = Field(None, description="本次查询所实际使用的语句")
    debug_info: Optional[Dict[str, Any]] = Field(None, description="本次查询的调试信息")


@app.post("/v1/query", response_model=QueryResponse, summary="执行自然语言查询", description="接收用户的自然语言输入，并根据选定的模式进行意图识别、数据库操作或对话生成。")
async def query(req: QueryRequest, request: Request, user_id: str = Depends(get_user_id)):
    if base_config is None:
        raise HTTPException(status_code=500, detail="Server configuration error")
        
    # 获取基础 URL (例如 http://localhost:10031/ 或 https://va-dev.soj.myds.me:1443/)
    # 考虑反向代理情况，检查 X-Forwarded-Proto
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    netloc = request.url.netloc
    base_url = f"{proto}://{netloc}"
    
    photo_path = None
    try:
        # Clone base config and modify for this request
        cfg = dataclasses.replace(base_config)
        cfg.user_id = user_id
        
        # ... (此处省略中间的照片处理和执行逻辑，保持原有逻辑) ...
        # 核心修复：确保执行层的 LLM API Key 使用的是服务器配置的密钥
        cfg.llm_api_key = os.environ.get("OPENAI_API_KEY", cfg.llm_api_key)
        cfg.llm_base_url = os.environ.get("OPENAI_BASE_URL", cfg.llm_base_url)
        
        cfg.user_token = None
        cfg.text_input = req.query
        cfg.no_tts = True
        cfg.use_cli = False  # Ensure direct database access
        
        if req.mode:
            mode_val = req.mode.value if hasattr(req.mode, "value") else req.mode
            cfg.execution_mode = mode_val
            
        # 1. 严格图片处理逻辑
        photo_path = None
        if req.photo:
            is_valid = False
            if req.photo.type == "url" and req.photo.url and str(req.photo.url).strip():
                is_valid = True
            elif req.photo.type == "base64" and req.photo.data and str(req.photo.data).strip():
                is_valid = True
                
            if is_valid:
                try:
                    photo_dir = os.path.join("data", "photos")
                    os.makedirs(photo_dir, exist_ok=True)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    unique_id = uuid.uuid4().hex[:8]
                    
                    def get_extension(mime: Optional[str]) -> str:
                        if not mime: return ".jpg"
                        m = mime.lower()
                        if "png" in m: return ".png"
                        if "gif" in m: return ".gif"
                        if "webp" in m: return ".webp"
                        return ".jpg"
                    
                    ext = get_extension(req.photo.mime)
                    
                    if req.photo.type == "base64":
                        b64_str = req.photo.data
                        if b64_str and "," in b64_str: b64_str = b64_str.split(",")[1]
                        if b64_str:
                            photo_bytes = base64.b64decode(b64_str)
                            filename = f"photo_{timestamp}_{unique_id}{ext}"
                            full_path = os.path.join(photo_dir, filename)
                            with open(full_path, "wb") as f: f.write(photo_bytes)
                            photo_path = f"photos/{filename}"
                    elif req.photo.type == "url":
                        import httpx
                        filename = f"photo_{timestamp}_{unique_id}{ext}"
                        full_path = os.path.join(photo_dir, filename)
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            resp = await client.get(req.photo.url)
                            if resp.status_code == 200:
                                with open(full_path, "wb") as f: f.write(resp.content)
                                photo_path = f"photos/{filename}"
                except Exception as photo_err:
                    log(f"Warning: Failed to process photo: {photo_err}", level="warn")
            
        if photo_path:
            cfg.force_record = True
            log(f"Photo attached and saved: {photo_path}, forcing record mode", level="info")

        result = await execute_transcript_async(cfg, req.query, photo_path=photo_path)

        reply_text = result.reply
        memories = []
        result_query = result.query
        
        if reply_text.strip().startswith("{") and reply_text.strip().endswith("}"):
            try:
                parsed = json.loads(reply_text)
                if isinstance(parsed, dict) and "reply" in parsed:
                    reply_text = str(parsed.get("reply", ""))
                    if "query" in parsed: result_query = str(parsed["query"])
                    if "memories" in parsed and isinstance(parsed["memories"], list):
                        result.memories = parsed["memories"]
            except Exception: pass

        # Map raw memories to MemoryItem and supplement full URLs
        for m in result.memories:
            p_path = m.get("photo_path")
            rel_url = get_photo_url(p_path)
            # 确保拼接时不会出现双斜杠：去掉 base_url 尾部斜杠，去掉 rel_url 开头斜杠，中间补一个
            full_url = f"{base_url}/{rel_url.lstrip('/')}" if rel_url else None
            
            memories.append(MemoryItem(
                id=str(m.get("id", "")),
                memory=m.get("memory", ""),
                created_at=str(m.get("created_at", "")),
                photo_path=p_path,
                photo_url=full_url,
                score=float(m.get("score") or 0.0)
            ))

        # Extract top 3 absolute photo URLs (ONLY from top 3 memories)
        images = []
        for m in memories[:3]:
            if m.photo_url:
                images.append(m.photo_url)
            if len(images) >= 3:
                break

        return QueryResponse(
            reply=reply_text,
            memories=memories,
            images=images,
            query=result_query or req.query,
            debug_info=result.debug_info
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/history", response_model=List[HistoryItem], summary="获取会话历史记录", description="按时间倒序返回当前用户的最近 20 条会话历史记录（包含请求文本和助手回复）。")
async def get_history(user_id: str = Depends(get_user_id)):
    try:
        histories = (SessionHistory
                    .select()
                    .where(SessionHistory.user_id == user_id)
                    .order_by(SessionHistory.created_at.desc())
                    .limit(20))
        return [
            HistoryItem(
                session_id=h.session_id,
                transcript=h.transcript,
                reply=h.reply,
                created_at=h.created_at.isoformat() if hasattr(h.created_at, "isoformat") else str(h.created_at)
            )
            for h in histories
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/memories", response_model=List[MemoryItem], summary="获取长期记忆条目", description="按时间倒序返回当前用户的最近 50 条长期记忆记录。")
async def get_memories(user_id: str = Depends(get_user_id)):
    try:
        memories = (RememberEntry
                   .select()
                   .where(RememberEntry.user_id == user_id)
                   .order_by(RememberEntry.created_at.desc())
                   .limit(50))
        return [
            MemoryItem(
                id=m.id,
                memory=m.memory,
                created_at=str(m.created_at),
                photo_path=m.photo_path,
                photo_url=get_photo_url(m.photo_path),
                score=0.0 # 列表接口暂无搜索评分
                )
                for m in memories
                ]

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def run_server():
    """Entry point for ptt-api command."""
    import uvicorn
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Run the Press-to-Talk API server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind the server to.")
    parser.add_argument("--port", type=int, default=10031, help="Port to bind the server to.")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload.")
    parser.add_argument("--workers", type=int, default=4, help="Number of worker processes.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    
    args = parser.parse_args()
    
    # Store verbose setting in environment so lifespan/init_session_log can pick it up
    if args.verbose:
        os.environ["PTT_LOG_LEVEL"] = "DEBUG"
        os.environ["PTT_VERBOSE"] = "1"
        from ..utils.logging import set_global_log_level
        set_global_log_level("DEBUG")
    
    # We use the string import pattern to allow reload to work correctly
    # Note: reload and workers are mutually exclusive in uvicorn
    if args.reload:
        uvicorn.run("press_to_talk.api.main:app", host=args.host, port=args.port, reload=True)
    else:
        uvicorn.run("press_to_talk.api.main:app", host=args.host, port=args.port, workers=args.workers)
