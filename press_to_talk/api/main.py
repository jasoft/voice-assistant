from fastapi import FastAPI, Depends, HTTPException, Security
from pydantic import BaseModel
from typing import List, Optional
import os

from .auth import get_user_id
from ..models.config import Config, parse_args
from ..execution import execute_transcript_async
from ..storage.models import SessionHistory, RememberEntry, db
from ..storage.service import load_storage_config

app = FastAPI(title="Press-to-Talk API")

# Initialize database on startup
@app.on_event("startup")
def startup():
    storage_cfg = load_storage_config()
    db_path = storage_cfg.history_db_path
    if not db_path:
        from ..storage.service import DEFAULT_HISTORY_DB_PATH
        db_path = str(DEFAULT_HISTORY_DB_PATH)
    
    db.init(db_path)
    db.connect(reuse_if_open=True)

class QueryRequest(BaseModel):
    query: str
    mode: Optional[str] = None

class QueryResponse(BaseModel):
    reply: str

@app.post("/v1/query", response_model=QueryResponse)
async def query(req: QueryRequest, user_id: str = Depends(get_user_id)):
    try:
        # Load config - parse_args uses env vars as defaults
        # We wrap it in a try-except because parse_args calls parser.error() on missing required env vars
        try:
            cfg = parse_args([])
        except SystemExit:
            # If parse_args failed, we might be missing critical env vars
            raise HTTPException(status_code=500, detail="Server configuration error (missing environment variables)")
            
        cfg.user_id = user_id
        cfg.text_input = req.query
        cfg.no_tts = True
        if req.mode:
            cfg.execution_mode = req.mode
            
        reply = await execute_transcript_async(cfg, req.query)
        return QueryResponse(reply=reply)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/history")
async def get_history(user_id: str = Depends(get_user_id)):
    try:
        histories = (SessionHistory
                    .select()
                    .where(SessionHistory.user_id == user_id)
                    .order_by(SessionHistory.created_at.desc())
                    .limit(20))
        return [
            {
                "session_id": h.session_id,
                "transcript": h.transcript,
                "reply": h.reply,
                "created_at": h.created_at.isoformat() if hasattr(h.created_at, "isoformat") else str(h.created_at)
            }
            for h in histories
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/memories")
async def get_memories(user_id: str = Depends(get_user_id)):
    try:
        memories = (RememberEntry
                   .select()
                   .where(RememberEntry.user_id == user_id)
                   .order_by(RememberEntry.created_at.desc())
                   .limit(50))
        return [
            {
                "id": m.id,
                "memory": m.memory,
                "created_at": str(m.created_at)
            }
            for m in memories
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
