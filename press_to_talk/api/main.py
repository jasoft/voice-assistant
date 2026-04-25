from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
import os
import dataclasses
from contextlib import asynccontextmanager

from .auth import get_user_id
from ..models.config import Config, parse_args
from ..execution import execute_transcript_async
from ..storage.models import SessionHistory, RememberEntry, db
from ..storage.service import load_storage_config

# Global base config to be loaded once at startup
base_config: Optional[Config] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database on startup
    global base_config
    storage_cfg = load_storage_config()
    db_path = storage_cfg.history_db_path
    if not db_path:
        from ..storage.service import DEFAULT_HISTORY_DB_PATH
        db_path = str(DEFAULT_HISTORY_DB_PATH)
    
    db.init(db_path)
    db.connect(reuse_if_open=True)
    
    # Load base config once
    try:
        # parse_args([]) loads defaults from env vars and config files
        base_config = parse_args([])
    except SystemExit:
        # If parse_args failed, we might be missing critical env vars
        # In a real API we might want to log this or handle more gracefully
        base_config = None
        
    yield
    # Cleanup on shutdown
    if not db.is_closed():
        db.close()

app = FastAPI(title="Press-to-Talk API", lifespan=lifespan)

from enum import Enum

class ExecutionMode(str, Enum):
    MEMORY_CHAT = "memory-chat"
    DATABASE = "database"
    HERMES = "hermes"
    INTENT = "intent"

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

    class Config:
        json_schema_extra = {
            "example": {
                "query": "最近三天的记录",
                "mode": "memory-chat"
            }
        }

class QueryResponse(BaseModel):
    reply: str

class HistoryItem(BaseModel):
    session_id: str
    transcript: str
    reply: str
    created_at: str

class MemoryItem(BaseModel):
    id: str
    memory: str
    created_at: str

@app.post("/v1/query", response_model=QueryResponse, summary="执行自然语言查询", description="接收用户的自然语言输入，并根据选定的模式进行意图识别、数据库操作或对话生成。")
async def query(req: QueryRequest, user_id: str = Depends(get_user_id)):
    if base_config is None:
        raise HTTPException(status_code=500, detail="Server configuration error")
        
    try:
        # Clone base config and modify for this request
        cfg = dataclasses.replace(base_config)
        cfg.user_id = user_id
        cfg.text_input = req.query
        cfg.no_tts = True
        cfg.use_cli = False  # Ensure direct database access
        
        if req.mode:
            cfg.execution_mode = req.mode.value if hasattr(req.mode, "value") else req.mode
            
        reply = await execute_transcript_async(cfg, req.query)
        return QueryResponse(reply=reply)
    except Exception as e:
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
                created_at=str(m.created_at)
            )
            for m in memories
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
    
    args = parser.parse_args()
    
    # We use the string import pattern to allow reload to work correctly
    uvicorn.run("press_to_talk.api.main:app", host=args.host, port=args.port, reload=args.reload)
