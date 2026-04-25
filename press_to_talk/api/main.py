from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
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

class QueryRequest(BaseModel):
    query: str
    mode: Optional[str] = None

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

@app.post("/v1/query", response_model=QueryResponse)
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
            cfg.execution_mode = req.mode
            
        reply = await execute_transcript_async(cfg, req.query)
        return QueryResponse(reply=reply)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/history", response_model=List[HistoryItem])
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

@app.post("/v1/memories", response_model=List[MemoryItem])
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
