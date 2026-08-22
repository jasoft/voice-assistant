from __future__ import annotations
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from types import SimpleNamespace
import os
import asyncio
import base64
import uuid
from datetime import datetime, timezone
import dataclasses
from contextlib import asynccontextmanager

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import json

from .auth import get_user_id
from ..models.config import Config, parse_args
from ..execution import execute_transcript_async
from ..storage.service import StorageService, load_storage_config, ensure_storage_database
from ..utils.logging import log, log_multiline
from ..utils.photo import get_photo_url
from ..harness import DeepSeekHarnessClient, HarnessError
from ..storage.models import SessionHistoryRecord
from ..storage.providers.mem0 import Mem0RememberStore



def mask_auth_header(auth_str: str) -> str:
    """Mask Authorization header for security, showing only first 6 and last 4 characters."""
    if not auth_str or len(auth_str) < 10:
        return "***"
    return f"{auth_str[:6]}...{auth_str[-4:]}"

# Global base config to be loaded once at startup
base_config: Optional[Config] = None
_harness_clients: dict[str, DeepSeekHarnessClient] = {}


def _uses_harness_backend() -> bool:
    backend = os.environ.get("PTT_QUERY_BACKEND", "legacy").strip().lower()
    return backend in {"harness", "deepseek-harness"}


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
        use_cli=False,
    )


def _mem0_store_for(user_id: str) -> Mem0RememberStore:
    config = load_storage_config(user_id_override=user_id)
    config.backend = "mem0"
    store = StorageService(config, use_cli=False).remember_store()
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

    record = SessionHistoryRecord(
        session_id=f"{harness_session_id or 'harness'}:{uuid.uuid4().hex}",
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        transcript=query,
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

    # Harness owns memory and tool orchestration. Do not initialize the legacy
    # PocketBase storage path when the query backend is explicitly Harness.
    if not _uses_harness_backend():
        storage_cfg = load_storage_config()
        ensure_storage_database(storage_cfg)
        
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
        
        # Log Response
        if request.url.path.startswith("/v1"):
            response_body = b""
            async for chunk in response.body_iterator:
                response_body += chunk
            
            # Re-wrap response iterator
            async def response_iterator():
                yield response_body
            response.body_iterator = response_iterator()

            try:
                res_str = response_body.decode("utf-8", errors="replace")
                if len(res_str) > 1000:
                    res_str = res_str[:1000] + "... [truncated]"
            except Exception:
                res_str = "[binary data]"

            log_multiline(f"API Response Sent (Status: {response.status_code})", res_str, level="info")

        return response

app = FastAPI(title="Press-to-Talk API", lifespan=lifespan)
os.makedirs("data/photos", exist_ok=True)
app.mount("/assets", StaticFiles(directory="data/photos"), name="assets")
app.add_middleware(LoggingMiddleware)

# -------------------------------

@app.get("/healthy", tags=["System"])
async def healthy():
    """Liveness probe: returns 200 OK if the server is running."""
    return {"status": "ok"}

@app.get("/ready", tags=["System"])
async def ready():
    """Readiness probe: returns 200 OK if the configurations are loaded."""
    if base_config is None:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    return {"status": "ready"}

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _execute_harness_query(
    *,
    user_id: str,
    req: QueryRequest,
    timeout_seconds: float | None = None,
) -> QueryResponse:
    kwargs: dict[str, Any] = {}
    if timeout_seconds is not None:
        kwargs["timeout_seconds"] = timeout_seconds
    harness_result = await _harness_client_for(user_id).query(
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
            mode=req.mode.value if req.mode else None,
            harness_session_id=harness_session_id,
        )
    except Exception as exc:
        raise HarnessHistoryPersistenceError("查询成功但写入会话历史失败") from exc
    return QueryResponse(**harness_result)


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
        service = StorageService(config, use_cli=False)
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
