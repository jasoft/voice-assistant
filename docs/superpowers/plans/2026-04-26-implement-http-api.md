# Implement HTTP API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a FastAPI-based HTTP API for Press-to-Talk with Bearer Token authentication and multi-user support.

**Architecture:** A lightweight FastAPI wrapper around the core execution engine. Authentication uses a database-backed token system (`APIToken` table). The core execution is called asynchronously.

**Tech Stack:** FastAPI, Uvicorn, Peewee (for auth lookup), press-to-talk core.

---

### Task 1: Create auth dependency

**Files:**
- Create: `press_to_talk/api/auth.py`

- [ ] **Step 1: Implement `get_user_id`**

```python
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from ..storage.models import APIToken

security = HTTPBearer()

async def get_user_id(auth: HTTPAuthorizationCredentials = Security(security)) -> str:
    token_str = auth.credentials
    try:
        token_obj = APIToken.get(APIToken.token == token_str)
        return token_obj.user_id
    except APIToken.DoesNotExist:
        raise HTTPException(status_code=401, detail="Invalid token")
```

- [ ] **Step 2: Create `press_to_talk/api/__init__.py` if it doesn't exist**

---

### Task 2: Update Config model

**Files:**
- Modify: `press_to_talk/models/config.py`

- [ ] **Step 1: Add `user_id` to `Config`**

Add `user_id: str = "soj"` to the `Config` dataclass. Ensure it has a default value to maintain backward compatibility with existing `parse_args` usage.

---

### Task 3: Implement FastAPI app

**Files:**
- Create: `press_to_talk/api/main.py`

- [ ] **Step 1: Implement the FastAPI application**

Implement `/v1/query`, `/v1/history`, and `/v1/memories`. For now, `history` and `memories` can be placeholders or basic implementations if requested, but Task 3 specifically asks for them.

```python
from fastapi import FastAPI, Depends
from pydantic import BaseModel
from .auth import get_user_id
from ..models.config import Config
from ..execution import execute_transcript_async
from ..utils.env import PROJECT_ROOT
import os

app = FastAPI(title="Press-to-Talk API")

class QueryRequest(BaseModel):
    query: str

@app.post("/v1/query")
async def query(req: QueryRequest, user_id: str = Depends(get_user_id)):
    # 1. Create a Config with user_id and text_input=True, no_tts=True
    # We need to be careful about how to instantiate Config since it has many fields.
    # We might need a helper to create a default config or use parse_args([]).
    from ..models.config import parse_args
    cfg = parse_args([])
    cfg.user_id = user_id
    cfg.text_input = req.query
    cfg.no_tts = True
    
    # 2. Call core execution
    reply = await execute_transcript_async(cfg, req.query)
    
    return {"reply": reply}

@app.post("/v1/history")
async def get_history(user_id: str = Depends(get_user_id)):
    return {"message": "History endpoint not fully implemented in Task 4 spec", "user_id": user_id}

@app.post("/v1/memories")
async def get_memories(user_id: str = Depends(get_user_id)):
    return {"message": "Memories endpoint not fully implemented in Task 4 spec", "user_id": user_id}
```

---

### Task 4: Verification and Commit

- [ ] **Step 1: Verify the API starts**

Run: `uvicorn press_to_talk.api.main:app --reload` (in a separate process or just check if it imports)

- [ ] **Step 2: Commit changes**

```bash
git add press_to_talk/api/auth.py press_to_talk/api/main.py press_to_talk/models/config.py
git commit -m "feat: implement fastapi with token auth and universal query endpoint"
```
