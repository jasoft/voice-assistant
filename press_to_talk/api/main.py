from __future__ import annotations
from fastapi import FastAPI, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, AsyncIterator
from types import SimpleNamespace
import os
import asyncio
import base64
import tempfile
import uuid
from pathlib import Path
from datetime import datetime, timezone
import dataclasses
from contextlib import asynccontextmanager

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import json

from .auth import get_user_id, uses_harness_backend as _uses_harness_backend
from ..models.config import Config, parse_args
from ..execution import execute_transcript_async
from ..storage.service import StorageService, load_storage_config
from ..utils.logging import log, log_multiline
from ..utils.photo import get_photo_url
from ..harness import DeepSeekHarnessClient, HarnessError
from ..reminders import (
    ReminderCreationError,
    cancel_remote_reminder,
    configured_store_path,
    create_reminder_from_text,
    load_reminder_records,
    save_reminder_records,
)
from ..storage.models import SessionHistoryRecord
from ..storage.providers.mem0 import Mem0RememberStore
from .fast_chat import try_fast_memory_chat
from .reply_stream import ReplyStream, current_stream, has_partial_reply, watch_prompt
from ..audio.stt import run_stt
from ..utils.text import current_time_with_weekday_text



def mask_auth_header(auth_str: str) -> str:
    """Mask Authorization header for security, showing only first 6 and last 4 characters."""
    if not auth_str or len(auth_str) < 10:
        return "***"
    return f"{auth_str[:6]}...{auth_str[-4:]}"

# Global base config to be loaded once at startup
base_config: Optional[Config] = None
_harness_clients: dict[str, DeepSeekHarnessClient] = {}
_chat_timeout_default = 30.0


def _chat_timeout_seconds() -> float:
    raw = os.environ.get("PTT_CHAT_TIMEOUT_SECONDS", str(_chat_timeout_default))
    try:
        value = float(raw)
    except ValueError:
        return _chat_timeout_default
    return value if value > 0 else _chat_timeout_default


def _chat_harness_client_for(user_id: str) -> DeepSeekHarnessClient:
    """Build a disposable one-shot client so chat requests share no context."""
    del user_id  # Mem0 and the fast agent are intentionally scoped to soj.
    return DeepSeekHarnessClient.from_env(
        agent_preset=os.environ.get("PTT_CHAT_HARNESS_AGENT_PRESET", "chat-fast"),
        timeout_seconds=_chat_timeout_seconds(),
        poll_interval_seconds=float(os.environ.get("PTT_CHAT_POLL_INTERVAL_SECONDS", "0.1")),
    )


def _harness_client_for(user_id: str) -> DeepSeekHarnessClient:
    client = _harness_clients.get(user_id)
    if client is None:
        client = DeepSeekHarnessClient.from_env()
        _harness_clients[user_id] = client
    return client


async def _close_harness_clients() -> None:
    clients = list(_harness_clients.values())
    _harness_clients.clear()
    for client in clients:
        await client.close()


def _history_service_for(user_id: str) -> StorageService:
    return StorageService(
        load_storage_config(user_id_override=user_id),

    )


def _mem0_store_for(user_id: str) -> Mem0RememberStore:
    config = load_storage_config(user_id_override=user_id)
    config.backend = "mem0"
    store = StorageService(config).remember_store()
    if not isinstance(store, Mem0RememberStore):
        raise RuntimeError("Mem0 记忆后端未启用：请配置 MEM0_API_KEY")
    return store


def _persist_harness_history(
    *,
    user_id: str,
    query: str,
    reply: str,
    mode: str | None,
    harness_session_id: str | None,
) -> None:
    """Persist one completed Harness turn to the durable PocketBase history."""

    clean_query = query
    if clean_query.startswith("【当前北京时间】"):
        parts = clean_query.split("\n", 1)
        if len(parts) > 1:
            clean_query = parts[1].lstrip()

    record = SessionHistoryRecord(
        session_id=f"{harness_session_id or 'harness'}:{uuid.uuid4().hex}",
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        transcript=clean_query,
        reply=reply,
        mode=mode or ExecutionMode.MEMORY_CHAT.value,
    )
    _history_service_for(user_id).history_store().persist(record)


class HarnessHistoryPersistenceError(RuntimeError):
    """Raised after Harness succeeds but durable history cannot be written."""


@dataclasses.dataclass
class _QueryJob:
    user_id: str
    query: str
    mode: ExecutionMode | None = None
    photo: dict[str, Any] | None = None
    status: str = "queued"
    reply: str | None = None
    error: str | None = None
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    task: asyncio.Task[Any] | None = None


_query_jobs: dict[str, _QueryJob] = {}


def _mem0_memory_items(user_id: str) -> list[MemoryItem]:
    records = _mem0_store_for(user_id).list_all_records()

    def created_key(record: Any) -> datetime:
        value = str(record.created_at or "").strip()
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    records.sort(key=created_key, reverse=True)
    return [
        MemoryItem(
            id=record.id,
            memory=record.memory,
            created_at=record.created_at,
            photo_path=None,
            photo_url=None,
            score=0.0,
        )
        for record in records
    ]


async def _harness_photo_payload(photo: PhotoAttachment | None) -> dict[str, Any] | None:
    if photo is None:
        return None
    payload = photo.model_dump()
    if photo.type == "base64":
        return payload
    if photo.type == "url" and photo.url:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(photo.url)
            response.raise_for_status()
        payload["data"] = base64.b64encode(response.content).decode("ascii")
        return payload
    return None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化文件日志
    from ..utils.logging import init_session_log, log, close_session_log
    from pathlib import Path
    log_path = init_session_log(Path("logs"), session_id="api-server")
    log(f"API Server started. Detailed logs at: {log_path}", level="info")

    global base_config
    # Load base config once (don't overwrite if already set by tests)
    if base_config is None:
        try:
            # Web API 使用进程环境和 .env 文件
            base_config = parse_args(["--user-id", "api-server", "--no-tts"], load_env=True)
        except SystemExit:
            base_config = None

    # Harness owns memory and tool orchestration; legacy storage initializes lazily.

    yield
    # Cleanup on shutdown
    log("API Server shutting down.", level="info")
    for job in _query_jobs.values():
        if job.task is not None and not job.task.done():
            job.task.cancel()
    await asyncio.gather(
        *(job.task for job in _query_jobs.values() if job.task is not None),
        return_exceptions=True,
    )
    await _close_harness_clients()
    close_session_log()

class LoggingMiddleware:
    """纯 ASGI 中间件：记录 /v1 请求与 JSON 响应。

    不用 BaseHTTPMiddleware：它对 StreamingResponse 的消息循环处理有缺陷
    （会以 "Unexpected message received: http.request" 中断流式响应）。
    这里响应消息全部透传不缓冲，流式端点（如 /v1/tts）得以边合成边下发。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/v1"):
            await self.app(scope, receive, send)
            return

        body = b""
        client_disconnected = False
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                client_disconnected = True
                break

        if client_disconnected:
            return

        # Prepare log content: Mask Authorization header（只影响日志，不改写 scope）
        raw_headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        headers = dict(raw_headers)
        if "authorization" in headers:
            headers["authorization"] = mask_auth_header(headers["authorization"])

        try:
            body_str = body.decode("utf-8", errors="replace")
        except Exception:
            body_str = "[binary data]"

        if len(body_str) > 1000:
            body_str = body_str[:1000] + "... [truncated]"

        client = scope.get("client")
        log_content = [
            f"Method: {scope.get('method', '?')}",
            f"URL: {scope.get('path', '?')}",
            f"Client: {client[0] if client else 'unknown'}",
            f"Headers: {json.dumps(headers, indent=2)}",
            f"Body: {body_str}",
        ]
        log_multiline("API Request Incoming", "\n".join(log_content), level="info")

        # Re-wrap body for subsequent handlers: body 只投递一次，之后透传原始
        # receive（保留真实的 http.disconnect 检测——StreamingResponse 靠它监听
        # 客户端断开，若谎报 disconnect 会立刻取消流式响应）
        body_sent = False
        original_receive = receive

        async def receive():
            nonlocal body_sent
            if body_sent:
                return await original_receive()
            body_sent = True
            return {"type": "http.request", "body": body}

        response_status: list[int] = []
        response_content_type: list[str] = []
        response_body = bytearray()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                response_status.append(message.get("status", 0))
                response_content_type.append(
                    next(
                        (
                            v.decode("latin-1")
                            for k, v in message.get("headers", [])
                            if k.decode("latin-1").lower() == "content-type"
                        ),
                        "",
                    )
                )
            elif message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if response_status:
                status = response_status[0]
                content_type = response_content_type[0] or ""
                if content_type.startswith("application/json"):
                    res_str = bytes(response_body).decode("utf-8", errors="replace")
                    if len(res_str) > 1000:
                        res_str = res_str[:1000] + "... [truncated]"
                else:
                    res_str = f"[binary data {len(response_body)} bytes]"
                log_multiline(f"API Response Sent (Status: {status})", res_str, level="info")

app = FastAPI(title="Press-to-Talk API", lifespan=lifespan)
os.makedirs("data/photos", exist_ok=True)
app.mount("/assets", StaticFiles(directory="data/photos"), name="assets")
app.add_middleware(LoggingMiddleware)

# -------------------------------

from ..version import get_version

@app.get("/healthy", tags=["System"])
async def healthy():
    """Liveness probe: returns 200 OK if the server is running."""
    return {"status": "ok", "version": get_version()}

@app.get("/ready", tags=["System"])
async def ready():
    """Readiness probe: returns 200 OK if the configurations are loaded."""
    if base_config is None:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    return {"status": "ready", "version": get_version()}

@app.get("/v1/version", tags=["System"])
async def get_version_endpoint():
    """Returns the current application version."""
    return {"version": get_version()}


@app.get("/debug", tags=["System"], response_class=HTMLResponse)
@app.get("/", tags=["System"], response_class=HTMLResponse)
async def api_debug_page():
    """Interactive Web Playground for API debugging, inspecting DeepSeek reasoning and streaming TTS."""
    debug_html_path = Path(__file__).parent / "static" / "debug.html"
    if debug_html_path.is_file():
        return HTMLResponse(
            content=debug_html_path.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
        )
    return HTMLResponse(
        content="<h1>Voice Assistant API Debugger</h1><p>static/debug.html not found</p>",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )



from enum import Enum

class ExecutionMode(str, Enum):
    """
    系统执行模式，决定了处理查询的底层架构。
    """
    MEMORY_CHAT = "memory-chat"
    DATABASE = "database"
    HERMES = "hermes"
    INTENT = "intent"

class PhotoAttachment(BaseModel):
    """
    图片附件信息。当提供此对象时，系统会强制进入 'record' 模式，将图片与查询内容关联并存入长期记忆。
    """
    type: str = Field(..., description="图片来源类型。'url': 从指定 URL 下载；'base64': 直接处理 Base64 编码的数据。")
    url: Optional[str] = Field(None, description="当 type 为 'url' 时必须提供有效的 HTTP(S) 链接。若为空且 type 为 'url'，该图片将被忽略。")
    data: Optional[str] = Field(None, description="当 type 为 'base64' 时必须提供。支持带前缀的 Data URI (如 'data:image/png;base64,...') 或纯 Base64 字符串。")
    mime: Optional[str] = Field(None, description="可选。图片的 MIME 类型（如 'image/png'）。若未提供，系统将根据内容猜测或默认为 '.jpg'。")

class QueryRequest(BaseModel):
    """
    自然语言查询请求对象。
    """
    query: str = Field(
        ..., 
        min_length=1,
        description=(
            "用户输入的原始文本。不能为空。对于纯图片记录需求，建议 Agent 自动填充描述性文本如 '记录这张照片'。\n"
            "该字段会经过意图识别，支持模糊日期（如 '昨天'）、实体检索等逻辑。"
        )
    )
    mode: Optional[ExecutionMode] = Field(
        default=ExecutionMode.MEMORY_CHAT, 
        description=(
            "执行策略选择：\n"
            "- `memory-chat` (推荐): 开启 RAG 模式。先检索相关记忆，再结合上下文生成回复，适合问答和聊天。\n"
            "- `database` / `intent`: 纯工具模式。只执行确定的 DB 操作，不进行发散，响应更快、更确定。\n"
            "- `hermes`: 强制透传给远程 Hermes 引擎处理。\n"
            "若设为 null，则默认使用 `memory-chat`。"
        )
    )
    photo: Optional[PhotoAttachment] = Field(
        None, 
        description="可选的图片附件。若提供，系统会将其持久化并与当前会话关联。空值将被安全忽略。"
    )
    stream: bool = Field(False, description="/v1/chat 返回 SSE：delta、done、error；默认完整 JSON。")
    response_style: str | None = Field(None, pattern="^watch$", description="watch：短段落、结论优先，适合手表显示和朗读。")
    selected_text: Optional[str] = Field(
        None,
        description=(
            "可选的选中文本上下文（GUI 在窗口激活前从上一个前台应用捕获）。"
            "提供后请求可走 改写/生成→粘贴 链路：当模型判定用户期望产出一段要插入"
            "光标处的内容时，回复的 action 为 'paste'，reply 即应粘贴的最终内容。"
        )
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "query": "最近三天的记录",
                "mode": "memory-chat",
                "photo": {
                    "type": "base64",
                    "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==",
                    "mime": "image/png",
                },
            }
        }
    }

class HistoryItem(BaseModel):
    """
    单条历史记录项。
    """
    session_id: str = Field(..., description="唯一会话 ID")
    transcript: str = Field(..., description="用户的原始请求文本")
    reply: str = Field(..., description="助手给出的回复文本")
    created_at: str = Field(..., description="记录创建时间 (ISO 8601 格式)")

class MemoryItem(BaseModel):
    """
    长期记忆条目详细信息。
    """
    id: str = Field(..., description="记忆条目唯一 ID")
    memory: str = Field(..., description="记忆的具体文本内容")
    created_at: str = Field(..., description="记忆存入的时间")
    photo_path: Optional[str] = Field(None, description="图片在服务器上的相对路径")
    photo_url: Optional[str] = Field(None, description="图片的完整绝对访问 URL。可直接用于前端展示。")
    score: float = Field(
        0.0, 
        description="相关性评分 (0.0 - 1.0)。分值越高代表与查询请求越匹配。仅在搜索请求中有效。"
    )

class QueryResponse(BaseModel):
    """
    查询执行结果响应对象。
    """
    reply: str = Field(..., description="助手生成的最终文本回复。")
    action: str = Field(
        "speak",
        description=(
            "建议的输出方式：'speak' 为常规回答（语音播报/展示）；"
            "'paste' 表示 reply 是应替换选中文本、或粘贴到目标窗口光标处的内容，"
            "调用方（如 Mac GUI）应以 Cmd+V 回贴而不是朗读。"
        )
    )
    reasoning: Optional[str] = Field(
        None,
        description="DeepSeek 或大模型的思考过程（Thinking / Reasoning），供调试与查看思考脉络。"
    )
    memories: List[MemoryItem] = Field(
        default_factory=list, 
        description="执行过程中检索到的相关记忆列表。按相关性降序排列。"
    )
    images: List[str] = Field(
        default_factory=list, 
        description="精选的相关图片 URL 列表。规则：取前 3 条得分 > 0 且包含图片的记忆。"
    )
    query: Optional[str] = Field(None, description="本次查询实际执行时的标准化文本（可能与输入不同）。")
    debug_info: Optional[Dict[str, Any]] = Field(None, description="包含推理路径、意图分析等调试信息，供开发者或 Agent 自我排查。")

class AudioAskResponse(QueryResponse):
    """语音提问响应：在 QueryResponse 基础上附带服务端 STT 识别出的文本。"""

    transcript: str = Field(..., description="服务端 STT 识别出的用户语音文本。")


class AsyncQueryResponse(BaseModel):
    """Acknowledgement for a long-running query accepted for background work."""

    job_id: str = Field(..., description="Opaque id used to poll this background query.")
    status: str = Field(..., description="Initial background-job status.")
    status_url: str = Field(..., description="Relative URL used to poll the job with the same Bearer token.")
    created_at: str = Field(..., description="Job creation time in ISO 8601 format.")

class QueryJobStatusResponse(BaseModel):
    """Current state and result of a background query job."""

    job_id: str = Field(..., description="Background job id.")
    status: str = Field(..., description="queued, running, succeeded, failed, or cancelled.")
    created_at: str = Field(..., description="Job creation time in ISO 8601 format.")
    started_at: Optional[str] = Field(None, description="Processing start time, when available.")
    completed_at: Optional[str] = Field(None, description="Processing completion time, when available.")
    query: str = Field(..., description="Original query text submitted for this job.")
    reply: Optional[str] = Field(None, description="Final assistant reply when status is succeeded.")
    error: Optional[str] = Field(None, description="Failure reason when status is failed or cancelled.")


class ReminderCreateRequest(BaseModel):
    """Natural-language reminder text supplied by the chat Agent."""

    text: str = Field(..., min_length=1, description="完整用户原话。")


class ReminderItem(BaseModel):
    """A QStash-backed reminder; field names match the Mac GUI store."""

    id: str
    qstash_message_id: str
    message: str
    scheduled_at: str
    created_at: str
    status: str
    is_recurring: bool = False
    cron_expression: Optional[str] = None
    timezone_identifier: Optional[str] = None
    schedule_description: Optional[str] = None




def _reminder_creation_kwargs() -> dict[str, Any]:
    missing = [
        name for name in ("QSTASH_URL", "QSTASH_TOKEN", "BARK_URL", "OPENAI_API_KEY", "PTT_MODEL")
        if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError(f"提醒配置缺失：{','.join(missing)}")
    return {
        "qstash_url": os.environ["QSTASH_URL"],
        "qstash_token": os.environ["QSTASH_TOKEN"],
        "bark_url": os.environ["BARK_URL"],
        "openai_api_key": os.environ["OPENAI_API_KEY"],
        "openai_base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "model": os.environ["PTT_MODEL"],
        "timezone_name": os.getenv("REMINDER_TIMEZONE", "Asia/Shanghai"),
        "group": os.getenv("REMINDER_GROUP", "Mac提醒"),
        "sound": os.getenv("REMINDER_SOUND") or None,
        "store_path": configured_store_path(),
    }


@app.post(
    "/v1/reminders",
    response_model=ReminderItem,
    status_code=201,
    summary="从自然语言创建云端手机提醒",
)
async def create_reminder(req: ReminderCreateRequest, user_id: str = Depends(get_user_id)):
    del user_id
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        timezone_name = os.getenv("REMINDER_TIMEZONE", "Asia/Shanghai")
        result = await asyncio.to_thread(
            create_reminder_from_text,
            req.text,
            now=datetime.now(ZoneInfo(timezone_name)),
            **_reminder_creation_kwargs(),
        )
    except ReminderCreationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log(f"Reminder creation failed: {exc}", level="error")
        raise HTTPException(status_code=500, detail="提醒创建失败") from exc

    records = load_reminder_records(configured_store_path())
    created = records[-1]
    return ReminderItem(**created)


@app.get("/v1/reminders", response_model=List[ReminderItem], summary="获取云端手机提醒")
async def list_reminders(user_id: str = Depends(get_user_id)):
    del user_id
    return [ReminderItem(**item) for item in load_reminder_records(configured_store_path())]


@app.delete(
    "/v1/reminders/{reminder_id}",
    response_model=ReminderItem,
    summary="取消云端手机提醒",
)
async def cancel_reminder(reminder_id: str, user_id: str = Depends(get_user_id)):
    del user_id
    store_path = configured_store_path()
    records = load_reminder_records(store_path)
    index = next((index for index, item in enumerate(records) if item.get("id") == reminder_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    record = records[index]
    if record.get("status") == "cancelled":
        return ReminderItem(**record)
    try:
        await asyncio.to_thread(
            cancel_remote_reminder,
            str(record["qstash_message_id"]),
            qstash_url=os.getenv("QSTASH_URL", ""),
            qstash_token=os.getenv("QSTASH_TOKEN", ""),
            is_recurring=bool(record.get("is_recurring")),
        )
    except ReminderCreationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    record["status"] = "cancelled"
    records[index] = record
    save_reminder_records(store_path, records)

    return ReminderItem(**record)

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _execute_harness_query(
    *,
    user_id: str,
    req: QueryRequest,
    timeout_seconds: float | None = None,
    client: DeepSeekHarnessClient | None = None,
    mode_name: str | None = None,
) -> QueryResponse:
    kwargs: dict[str, Any] = {}
    if timeout_seconds is not None:
        kwargs["timeout_seconds"] = timeout_seconds
    harness_result = await (client or _harness_client_for(user_id)).query(
        req.query,
        photo=await _harness_photo_payload(req.photo),
        **kwargs,
    )
    debug_info = harness_result.get("debug_info")
    harness_session_id = (
        str(debug_info.get("session_id"))
        if isinstance(debug_info, dict) and debug_info.get("session_id")
        else None
    )
    try:
        _persist_harness_history(
            user_id=user_id,
            query=str(harness_result.get("query") or req.query),
            reply=str(harness_result.get("reply", "")),
            mode=mode_name or (req.mode.value if req.mode else None),
            harness_session_id=harness_session_id,
        )
    except Exception as exc:
        raise HarnessHistoryPersistenceError("查询成功但写入会话历史失败") from exc
    return QueryResponse(**harness_result)


async def _handle_chat(req: QueryRequest, user_id: str) -> QueryResponse:
    """Run a stateless one-shot request.

    Fast path (≤ 8 s): memory record/find queries are handled directly via
    Memos REST API + a single LLM summarization call, bypassing the Harness Agent.

    Slow path: everything else goes through the DeepSeek Harness chat-fast
    Agent as before.
    """
    if base_config is None:
        raise HTTPException(status_code=500, detail="Server configuration error")

    # --- Fast path: direct memory operations ---
    try:
        fast_result = await try_fast_memory_chat(req.query, selected_text=req.selected_text)
        if fast_result is not None:
            log(
                f"fast-chat: served in {fast_result.get('debug_info', {}).get('elapsed_s', '?')}s",
                level="info",
            )
            # Persist to history
            try:
                _persist_harness_history(
                    user_id=user_id,
                    query=req.query,
                    reply=str(fast_result.get("reply", "")),
                    mode="chat-fast",
                    harness_session_id=None,
                )
            except Exception as exc:
                log(f"fast-chat: history persistence failed: {exc}", level="warn")

            memories_out: list[MemoryItem] = []
            for m in fast_result.get("memories", []):
                memories_out.append(MemoryItem(
                    id=str(m.get("id", "")),
                    memory=str(m.get("memory", "")),
                    created_at=str(m.get("created_at", "")),
                    photo_path=None,
                    photo_url=None,
                    score=float(m.get("score") or 0.0),
                ))
            return QueryResponse(
                reply=str(fast_result.get("reply", "")),
                action=str(fast_result.get("action") or "speak"),
                reasoning=fast_result.get("reasoning"),
                memories=memories_out,
                images=[],
                query=fast_result.get("query") or req.query,
                debug_info=fast_result.get("debug_info"),
            )
    except Exception as exc:
        if has_partial_reply():
            raise
        log(f"fast-chat: fast path failed, falling back to Harness: {exc}", level="warn")

    # --- Slow path: Harness Agent fallback ---
    if not _uses_harness_backend():
        raise HTTPException(status_code=404, detail="聊天端点仅在 Harness 模式可用")

    # 慢路径注入当前时间保底，杜绝模型幻觉当前日期
    current_time = current_time_with_weekday_text()
    time_prefix = f"【当前北京时间】{current_time}\n"
    slow_query = f"{time_prefix}{req.query}"

    # 慢路径没有 delivery 判定，退化为把选中文本作为上下文拼进问句（播报回答）。
    if req.selected_text and req.selected_text.strip():
        selection = req.selected_text.strip()
        if len(selection) > 20000:
            selection = selection[:20000]
        slow_query = f"{slow_query}\n\n【用户当前选中的文本】\n{selection}"

    if watch_prompt():
        slow_query += "\n\n" + watch_prompt()
    slow_req = req.model_copy(update={"query": slow_query})

    try:
        client = _chat_harness_client_for(user_id)
        try:
            harness_resp = await asyncio.wait_for(
                _execute_harness_query(
                    user_id=user_id,
                    req=slow_req,
                    client=client,
                    mode_name="chat",
                    timeout_seconds=_chat_timeout_seconds(),
                ),
                timeout=_chat_timeout_seconds(),
            )
            # 保证对外返回的 query 为用户原始输入，不暴露系统前缀
            if harness_resp.query and harness_resp.query.startswith("【当前北京时间】"):
                parts = harness_resp.query.split("\n", 1)
                clean_q = parts[1].lstrip() if len(parts) > 1 else req.query
                harness_resp = harness_resp.model_copy(update={"query": clean_q})
            return harness_resp
        finally:
            await client.close()
    except asyncio.TimeoutError as exc:
        timeout_seconds = _chat_timeout_seconds()
        log(f"Chat timed out after {timeout_seconds:g}s", level="warn")
        raise HTTPException(
            status_code=504,
            detail=f"聊天处理超过 {timeout_seconds:g} 秒",
        ) from exc
    except HarnessError as exc:
        log(f"DeepSeek Harness chat failed: {exc}", level="error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except HarnessHistoryPersistenceError as exc:
        log(f"DeepSeek Harness chat history persistence failed: {exc}", level="error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        log(f"Chat processing failed: {exc}", level="error")
        raise HTTPException(status_code=500, detail="聊天处理失败") from exc


def _stream_chat(req: QueryRequest, user_id: str) -> StreamingResponse:
    async def events():
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        async def send(delta):
            await queue.put(("delta", {"text": delta}))
        async def produce():
            context = ReplyStream(send, watch=req.response_style == "watch")
            token = current_stream.set(context)
            try:
                result = await asyncio.wait_for(_handle_chat(req, user_id), timeout=_chat_timeout_seconds())
                if not result.reply.strip():
                    raise RuntimeError("回答为空，请重新提问")
                if not context.text:
                    # Tool execution and memo writes are atomic; only publish after success.
                    await context.emit(result.reply)
                elif context.text != result.reply:
                    raise RuntimeError("流式回答与最终结果不一致")
                await queue.put(("done", result.model_dump(mode="json")))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = exc.detail if isinstance(exc, HTTPException) else ("回答等待超时" if isinstance(exc, TimeoutError) else "回答中断，请重新提问")
                log(f"chat stream failed: {type(exc).__name__}", level="warn")
                await queue.put(("error", {"message": message}))
            finally:
                current_stream.reset(token)
        task = asyncio.create_task(produce())
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if event in {"done", "error"}:
                    break
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform",
    })


@app.post(
    "/v1/chat",
    response_model=QueryResponse,
    summary="[极速] 一次性聊天与记忆问答",
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
    description=(
        "单次请求自动分流：明确的记忆记录或查询走 Memos，普通问题走模型或 Harness。"
        "stream 默认 false 返回完整 JSON；true 返回 SSE delta/done/error。"
        "模型增量实时透传，工具和写入确认在成功后输出。"
        "response_style=watch 启用手表输出提示词（流式请求）。"
        "每次调用使用独立会话，不携带上一轮上下文。"
    ),
)
async def chat(req: QueryRequest, user_id: str = Depends(get_user_id)):
    return _stream_chat(req, user_id) if req.stream else await _handle_chat(req, user_id)


@app.post(
    "/chat",
    response_model=QueryResponse,
    include_in_schema=False,
)
async def chat_alias(req: QueryRequest, user_id: str = Depends(get_user_id)):
    return _stream_chat(req, user_id) if req.stream else await _handle_chat(req, user_id)


_ASK_AUDIO_MIN_SECONDS = 0.4


def _wav_duration_seconds(path: Path) -> Optional[float]:
    """Return WAV duration in seconds, or None when the container is not WAV."""
    import wave

    try:
        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate() or 1
            return wav.getnframes() / rate
    except (wave.Error, EOFError):
        return None


async def _transcribe_upload(file: UploadFile) -> str:
    """落盘上传的录音并调用服务端 STT，返回识别文本。"""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="音频内容为空")
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        audio_path = Path(tmp.name)
    try:
        duration = await asyncio.to_thread(_wav_duration_seconds, audio_path)
        if duration is not None and duration < _ASK_AUDIO_MIN_SECONDS:
            raise HTTPException(status_code=400, detail="录音太短，请说完再停止录音")
        stt_url = os.environ.get("PTT_STT_URL", "").strip()
        if not stt_url:
            raise HTTPException(status_code=503, detail="STT 服务未配置（PTT_STT_URL）")
        transcript = await asyncio.to_thread(
            run_stt, stt_url, os.environ.get("PTT_STT_TOKEN", ""), audio_path
        )
    finally:
        audio_path.unlink(missing_ok=True)
    transcript = (transcript or "").strip()
    if not transcript:
        raise HTTPException(status_code=422, detail="未能识别到语音内容")
    return transcript


@app.post(
    "/v1/ask-audio",
    response_model=AudioAskResponse,
    summary="语音提问：上传录音，服务端转写后走聊天链路",
    description=(
        "接收 multipart 上传的录音（推荐 16kHz 单声道 WAV，与客户端录音格式一致）。"
        "服务端先 STT 转写，再复用 /v1/chat 的 fast-path → Harness 链路回答，"
        "响应额外携带 transcript 字段。"
    ),
)
async def ask_audio(file: UploadFile = File(...), user_id: str = Depends(get_user_id)):
    transcript = await _transcribe_upload(file)
    log(f"ask-audio: transcript={transcript[:80]}", level="info")
    response = await _handle_chat(QueryRequest(query=transcript), user_id)
    return AudioAskResponse(transcript=transcript, **response.model_dump())


class TranscribeResponse(BaseModel):
    """仅转写响应：客户端先展示识别文本，再决定是否继续问答。"""

    transcript: str = Field(..., description="服务端 STT 识别出的用户语音文本。")


@app.post(
    "/v1/transcribe",
    response_model=TranscribeResponse,
    summary="仅语音转写：上传录音，返回识别文本（不触发问答）",
    description=(
        "与 /v1/ask-audio 的转写步骤一致，但只做 STT；客户端可先展示/确认识别文本，"
        "再单独调用 /v1/chat 继续问答。"
    ),
)
async def transcribe(file: UploadFile = File(...), user_id: str = Depends(get_user_id)):
    del user_id
    transcript = await _transcribe_upload(file)
    log(f"transcribe: transcript={transcript[:80]}", level="info")
    return TranscribeResponse(transcript=transcript)


class TTSRequest(BaseModel):
    """文本转语音请求。"""

    text: str = Field(..., min_length=1, max_length=2000, description="要合成的文本（最长 2000 字符）。")


_TTS_MAX_CHARS = 2000


async def _qwen_tts_stream(text: str) -> AsyncIterator[bytes]:
    """流式调用阿里云百炼（DashScope）Qwen TTS（SSE），逐块 yield 16-bit LE PCM 字节（按 2 字节对齐）。

    Key 来自 .env 加载后的 DASHSCOPE_API_KEY；API 地址可用 DASHSCOPE_BASE_URL 覆盖；
    模型与音色可用 PTT_TTS_MODEL / PTT_TTS_VOICE 覆盖。输出固定
    24kHz/16bit/单声道 PCM（mimeType: audio/l16）。
    """
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("TTS 未配置（DASHSCOPE_API_KEY）")
    base_url = (
        os.environ.get("DASHSCOPE_BASE_URL", "").strip()
        or "https://llm-p6d84x694t9d455p.cn-beijing.maas.aliyuncs.com/api/v1"
    ).rstrip("/")
    url = f"{base_url}/services/aigc/multimodal-generation/generation"

    raw_model = os.environ.get("PTT_TTS_MODEL", "qwen3-tts-flash").strip()
    # 语音输入口误/兼容：将 qwen-audio-3.1-tts-flash 等映射为 qwen3-tts-flash
    model = "qwen3-tts-flash" if "qwen-audio-3.1-tts-flash" in raw_model else raw_model
    voice = os.environ.get("PTT_TTS_VOICE", "Cherry").strip()

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-DashScope-SSE": "enable",
    }
    payload = {
        "model": model,
        "input": {"text": text[:_TTS_MAX_CHARS]},
        "parameters": {"voice": voice},
    }
    import httpx

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise RuntimeError(f"Qwen TTS 上游返回 {resp.status_code}：{body}")
                carry = b""
                header_stripped = False
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if "code" in chunk and str(chunk.get("code")) != "200":
                        msg = chunk.get("message") or chunk.get("code")
                        raise RuntimeError(f"Qwen TTS 上游错误：{msg}")
                    audio_data = (chunk.get("output") or {}).get("audio", {}).get("data")
                    if audio_data:
                        carry += base64.b64decode(audio_data)
                        if not header_stripped:
                            if carry.startswith(b"RIFF"):
                                data_idx = carry.find(b"data")
                                if data_idx != -1 and len(carry) >= data_idx + 8:
                                    carry = carry[data_idx + 8 :]
                                    header_stripped = True
                                elif len(carry) > 100:
                                    header_stripped = True
                                else:
                                    continue
                            else:
                                header_stripped = True
                        if len(carry) % 2:
                            carry, out = carry[-1:], carry[:-1]
                        else:
                            carry, out = b"", carry
                        if out:
                            yield out
                if len(carry) >= 2:
                    yield carry
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Qwen TTS 连接失败：{exc}") from exc


async def _gemini_tts_stream(text: str) -> AsyncIterator[bytes]:
    """流式调用 Gemini TTS（SSE），逐块 yield 16-bit LE PCM 字节（按 2 字节对齐）。

    Key 来自 .env 加载后的 GOOGLE_AI_STUDIO_KEY；模型/音色可用
    PTT_TTS_GEMINI_MODEL / PTT_TTS_GEMINI_VOICE 覆盖。输出固定
    24kHz/16bit/单声道 PCM（mimeType: audio/l16）。
    """
    key = os.environ.get("GOOGLE_AI_STUDIO_KEY", "").strip()
    if not key:
        raise RuntimeError("TTS 未配置（GOOGLE_AI_STUDIO_KEY）")
    model = os.environ.get("PTT_TTS_GEMINI_MODEL", "gemini-3.8-flash-lite-tts")
    voice = os.environ.get("PTT_TTS_GEMINI_VOICE", "Kore")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:streamGenerateContent?alt=sse&key={key}"
    )
    payload = {
        "contents": [{"parts": [{"text": text[:_TTS_MAX_CHARS]}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
            },
        },
    }
    import httpx

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", url, json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    raise RuntimeError(f"Gemini TTS 上游返回 {resp.status_code}：{body}")
                carry = b""
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    parts = (
                        (chunk.get("candidates") or [{}])[0]
                        .get("content", {})
                        .get("parts", [])
                    )
                    for part in parts:
                        inline = part.get("inlineData") or {}
                        if inline.get("data"):
                            carry += base64.b64decode(inline["data"])
                            if len(carry) % 2:
                                carry, out = carry[-1:], carry[:-1]
                            else:
                                carry, out = b"", carry
                            if out:
                                yield out
                if len(carry) >= 2:
                    yield carry
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Gemini TTS 连接失败：{exc}") from exc


_original_gemini_tts_stream = _gemini_tts_stream
_original_qwen_tts_stream = _qwen_tts_stream


async def _tts_stream(text: str) -> AsyncIterator[bytes]:
    """流式 TTS 统一入口：优先使用阿里云百炼 Qwen TTS，未配置时回退到 Gemini TTS。"""
    if os.environ.get("DASHSCOPE_API_KEY", "").strip():
        async for chunk in _qwen_tts_stream(text):
            yield chunk
        return
    if os.environ.get("GOOGLE_AI_STUDIO_KEY", "").strip():
        async for chunk in _gemini_tts_stream(text):
            yield chunk
        return
    raise RuntimeError("TTS 未配置（DASHSCOPE_API_KEY）")


_original_tts_stream = _tts_stream


@app.post(
    "/v1/tts",
    summary="文本转语音：Qwen/Gemini TTS 流式合成，返回 24kHz/16bit/单声道 PCM 字节流",
    description=(
        "接收 {\"text\": ...}，服务端流式调用 qwen3-tts-flash，"
        "边合成边推送裸 PCM（16bit LE/24kHz/单声道）。客户端可边收边播，"
        "点按即播无需等全量。"
    ),
)
async def tts(req: TTSRequest, user_id: str = Depends(get_user_id)):
    del user_id
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="文本为空")
    if _tts_stream is not _original_tts_stream:
        stream = _tts_stream(text)
    elif _gemini_tts_stream is not _original_gemini_tts_stream:
        stream = _gemini_tts_stream(text)
    elif _qwen_tts_stream is not _original_qwen_tts_stream:
        stream = _qwen_tts_stream(text)
    else:
        stream = _tts_stream(text)
    # 快速失败：首块拿到再返回流式响应，连接/鉴权错误在这里变成 502。
    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        first = b""
    except RuntimeError as exc:
        log(f"tts: {exc}", level="error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    async def _gen() -> AsyncIterator[bytes]:
        if first:
            yield first
        try:
            async for chunk in stream:
                yield chunk
        except RuntimeError as exc:
            # 流式响应已发出，状态码无法更改；记录原因并优雅收尾
            log(f"tts: 流中断：{exc}", level="error")

    return StreamingResponse(
        _gen(),
        media_type="application/octet-stream",
        headers={
            "X-Audio-Format": "pcm;rate=24000;channels=1;bits=16",
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
        },
    )


@app.post(
    "/v1/query", 
    response_model=QueryResponse, 
    summary="[核心] 执行自然语言查询", 
    description=(
        "接收自然语言输入，自动完成意图识别、实体检索、记忆调取及响应生成。\n\n"
        "### Agent 调用建议：\n"
        "1. **场景判断**：若用户提到‘刚才、之前、哪里、谁’等涉及过去信息的词汇，请务必保持 `memory-chat` 模式。\n"
        "2. **图片处理**：当用户上传图片时，系统会自动开启存储逻辑，无需额外声明‘请记录’。\n"
        "3. **异常处理**：若返回 `reply` 中包含错误提示，可查看 `debug_info` 获取详细执行链。"
    )
)
async def query(req: QueryRequest, request: Request, user_id: str = Depends(get_user_id)):
    if base_config is None:
        raise HTTPException(status_code=500, detail="Server configuration error")

    if _uses_harness_backend():
        try:
            return await _execute_harness_query(
                user_id=user_id,
                req=req,
            )
        except HarnessError as exc:
            log(f"DeepSeek Harness query failed: {exc}", level="error")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except HTTPException:
            raise
        except HarnessHistoryPersistenceError as exc:
            log(f"DeepSeek Harness history persistence failed: {exc}", level="error")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            log(
                f"DeepSeek Harness result post-processing failed: {exc}",
                level="error",
            )
            raise HTTPException(
                status_code=500,
                detail="查询成功但写入会话历史失败",
            ) from exc


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
        
        # 核心修复：确保执行层的 LLM API Key 使用的是服务器配置的密钥
        cfg.llm_api_key = os.environ.get("OPENAI_API_KEY", cfg.llm_api_key)
        cfg.llm_base_url = os.environ.get("OPENAI_BASE_URL", cfg.llm_base_url)
        
        cfg.user_token = None
        cfg.text_input = req.query
        cfg.no_tts = True

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

        # Generate session metadata for history persistence
        session_id = uuid.uuid4().hex
        started_at = datetime.now().astimezone().isoformat(timespec="seconds")

        query_timeout_seconds = float(os.environ.get("PTT_QUERY_TIMEOUT_SECONDS", "60"))
        try:
            result = await asyncio.wait_for(
                execute_transcript_async(
                    cfg,
                    req.query,
                    photo_path=photo_path,
                    session_id=session_id,
                    started_at=started_at,
                    session_mode="api",
                ),
                timeout=query_timeout_seconds,
            )
        except asyncio.TimeoutError:
            log(
                f"Execution timed out after {query_timeout_seconds:.1f}s: query={req.query}",
                level="error",
            )
            result = SimpleNamespace(
                reply="这次查询处理超时，请稍后再试。",
                memories=[],
                query=req.query,
                debug_info={"timeout_seconds": query_timeout_seconds},
                error=None,
            )

        if result.error:
            log(f"Execution Error: {result.error}", level="error")
            raise HTTPException(status_code=500, detail=result.error)

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

        # Extract top 3 absolute photo URLs (ONLY from top 3 memories with score > 0)
        images = []
        for m in memories[:3]:
            if m.photo_url and m.score > 0:
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

def _prune_query_jobs() -> None:
    """Bound the in-memory job table while preserving active results."""

    finished = [
        (job_id, job)
        for job_id, job in _query_jobs.items()
        if job.status in {"succeeded", "failed", "cancelled"}
    ]
    excess = max(0, len(finished) - 200)
    for job_id, _job in sorted(finished, key=lambda item: item[1].created_at)[:excess]:
        _query_jobs.pop(job_id, None)


async def _run_query_job(job_id: str) -> None:
    job = _query_jobs.get(job_id)
    if job is None or job.status not in {"queued"}:
        return

    job.status = "running"
    job.started_at = _utc_now()
    try:
        timeout_seconds = float(os.environ.get("PTT_HARNESS_ASYNC_TIMEOUT_SECONDS", "300"))
        result = await _execute_harness_query(
            user_id=job.user_id,
            req=QueryRequest(query=job.query, mode=job.mode, photo=job.photo),
            timeout_seconds=timeout_seconds,
        )
        job.reply = result.reply
        job.status = "succeeded"
    except HarnessError as exc:
        job.error = str(exc)
        job.status = "failed"
    except HarnessHistoryPersistenceError as exc:
        job.error = str(exc)
        job.status = "failed"
        log(f"Async query {job_id} history persistence failed: {exc}", level="error")
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.error = "查询已取消"
        raise
    except Exception as exc:
        job.error = "后台查询执行失败"
        job.status = "failed"
        log(f"Async query {job_id} failed: {exc}", level="error")
    finally:
        if job.completed_at is None:
            job.completed_at = _utc_now()


@app.post(
    "/v1/query/async",
    response_model=AsyncQueryResponse,
    status_code=202,
    summary="[低延迟] 提交后台查询",
    description="立即返回 job_id；调用方轮询状态接口，避免长连接等待模型生成。",
)
async def start_async_query(req: QueryRequest, user_id: str = Depends(get_user_id)):
    if base_config is None:
        raise HTTPException(status_code=500, detail="Server configuration error")
    if not _uses_harness_backend():
        raise HTTPException(status_code=404, detail="后台查询仅在 Harness 模式可用")

    _prune_query_jobs()
    job_id = uuid.uuid4().hex
    job = _QueryJob(
        user_id=user_id,
        query=req.query,
        mode=req.mode,
        photo=await _harness_photo_payload(req.photo),
        created_at=_utc_now(),
    )
    _query_jobs[job_id] = job
    job.task = asyncio.create_task(_run_query_job(job_id))
    return AsyncQueryResponse(
        job_id=job_id,
        status=job.status,
        status_url=f"/v1/query/status/{job_id}",
        created_at=job.created_at,
    )


@app.get(
    "/v1/query/status/{job_id}",
    response_model=QueryJobStatusResponse,
    summary="查询后台任务结果",
)
async def get_query_job_status(job_id: str, user_id: str = Depends(get_user_id)):
    job = _query_jobs.get(job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="查询任务不存在")
    return QueryJobStatusResponse(
        job_id=job_id,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        query=job.query,
        reply=job.reply,
        error=job.error,
    )


@app.post("/v1/history", response_model=List[HistoryItem], summary="获取会话历史记录", description="按时间倒序返回当前用户的最近 20 条会话历史记录（包含请求文本和助手回复）。")
async def get_history(user_id: str = Depends(get_user_id)):
    try:
        service = _history_service_for(user_id)
        records = service.history_store().list_recent(limit=20)
        return [
            HistoryItem(
                session_id=r.session_id,
                transcript=r.transcript,
                reply=r.reply,
                created_at=r.started_at
            )
            for r in records
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/memories", response_model=List[MemoryItem], summary="获取长期记忆条目", description="按时间倒序返回当前用户的最近 50 条长期记忆记录。")
async def get_memories(user_id: str = Depends(get_user_id)):
    if _uses_harness_backend():
        try:
            return _mem0_memory_items(user_id)
        except Exception as exc:
            log(f"Mem0 memory listing failed: {exc}", level="error")
            raise HTTPException(
                status_code=502,
                detail=f"无法读取 Mem0 记忆：{exc}",
            ) from exc

    try:
        config = load_storage_config(user_id_override=user_id)
        service = StorageService(config)
        records = service.remember_store().list_all(limit=50)
        return [
            MemoryItem(
                id=m.id,
                memory=m.memory,
                created_at=m.created_at,
                photo_path=m.photo_path,
                photo_url=get_photo_url(m.photo_path),
                score=0.0 
                )
                for m in records
                ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def run_server():
    """Entry point for ptt-api command."""
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="Run the Press-to-Talk API server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind the server to.")
    parser.add_argument("--port", type=int, default=10031, help="Port to bind the server to.")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload.")
    # Query jobs and Harness client sessions are process-local. Keep one worker
    # so status polling always reaches the same state as submission.
    parser.add_argument("--workers", type=int, default=1, help="Number of worker processes.")
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
