# API and Multi-user Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a FastAPI-based HTTP API with Token-based multi-user isolation using Peewee ORM.

**Architecture:**
- Use Peewee for database models and migration.
- Extend `StorageService` and underlying stores to handle `user_id`.
- Implement FastAPI application with Bearer Token authentication.
- Reuse `ptt-voice` core engine via its Python API for natural language queries.

**Tech Stack:** Python, FastAPI, Peewee, SQLite, Uvicorn.

---

### Task 1: Add Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `peewee` to `pyproject.toml`**

Add `peewee>=3.17.0` to the `dependencies` list in `pyproject.toml`.

- [ ] **Step 2: Install dependencies**

Run: `uv sync`
Expected: `peewee` is installed.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add peewee dependency"
```

---

### Task 2: Implement Peewee Models

**Files:**
- Modify: `press_to_talk/storage/models.py`

- [ ] **Step 1: Define Peewee Models**

Define `APIToken`, `SessionHistory`, and `RememberEntry` using Peewee's `Model` class. Ensure `user_id` is present in `SessionHistory` and `RememberEntry`.

```python
from peewee import Model, CharField, DateTimeField, FloatField, BooleanField, TextField, SqliteDatabase

db = SqliteDatabase(None) # To be initialized in service

class BaseModel(Model):
    class Meta:
        database = db

class APIToken(BaseModel):
    token = CharField(primary_key=True)
    user_id = CharField(index=True)
    description = TextField(null=True)
    created_at = DateTimeField(constraints=[SQL('DEFAULT CURRENT_TIMESTAMP')])

class SessionHistory(BaseModel):
    session_id = CharField(unique=True)
    user_id = CharField(index=True)
    # ... other fields
```

- [ ] **Step 2: Update `StorageConfig`**

Add `user_id: str = "soj"` to `StorageConfig` dataclass.

- [ ] **Step 3: Commit**

```bash
git add press_to_talk/storage/models.py
git commit -m "feat: implement peewee models and update storage config"
```

---

### Task 3: Refactor Storage Layer

**Files:**
- Modify: `press_to_talk/storage/service.py`
- Modify: `press_to_talk/storage/sqlite_history.py`
- Modify: `press_to_talk/storage/providers/sqlite_fts.py`

- [ ] **Step 1: Update `StorageService` to initialize Peewee**

In `StorageService.__init__`, initialize the Peewee database proxy with the configured path.

- [ ] **Step 2: Implement `PeeweeHistoryStore`**

Rewrite `SQLiteHistoryStore` using Peewee. Ensure `user_id` is used in all `select()`, `insert()`, and `delete()` operations.

- [ ] **Step 3: Implement `PeeweeRememberStore`**

Rewrite `SQLiteFTS5RememberStore` using Peewee. Handle the FTS5 virtual table manually if needed or via Peewee's FTS5 support.

- [ ] **Step 4: Commit**

```bash
git add press_to_talk/storage/service.py press_to_talk/storage/sqlite_history.py press_to_talk/storage/providers/sqlite_fts.py
git commit -m "feat: refactor storage stores to use peewee and support user isolation"
```

---

### Task 4: Implement HTTP API

**Files:**
- Create: `press_to_talk/api/auth.py`
- Create: `press_to_talk/api/main.py`
- Modify: `press_to_talk/models/config.py`

- [ ] **Step 1: Create `press_to_talk/api/auth.py`**

Implement a FastAPI dependency that extracts the Bearer Token and looks up the `user_id` in the `APIToken` table.

- [ ] **Step 2: Update `Config` model**

Add `user_id: str = "soj"` to `Config` in `press_to_talk/models/config.py`.

- [ ] **Step 3: Create `press_to_talk/api/main.py`**

Implement FastAPI app with `POST /v1/query`, `POST /v1/history`, and `POST /v1/memories`.
The `/v1/query` endpoint should:
1. Get `user_id` from auth.
2. Create a `Config` with `user_id` and `text_input=True`, `no_tts=True`.
3. Call `press_to_talk.core.execute_transcript_async`.
4. Return the reply.

- [ ] **Step 4: Commit**

```bash
git add press_to_talk/api/auth.py press_to_talk/api/main.py press_to_talk/models/config.py
git commit -m "feat: implement fastapi with token auth and universal query endpoint"
```

---

### Task 5: Migration and Entry Points

**Files:**
- Create: `scripts/migrate_v2_peewee.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create migration script**

Write a script to migrate data from the old SQLite schema to the new Peewee-managed schema, setting `user_id='soj'` for existing records.

- [ ] **Step 2: Add API script to `pyproject.toml`**

Add `ptt-api = "press_to_talk.api.main:run_server"` to `project.scripts`.

- [ ] **Step 3: Run migration**

Run: `python3 scripts/migrate_v2_peewee.py`
Expected: Data is migrated without errors.

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate_v2_peewee.py pyproject.toml
git commit -m "feat: add migration script and api entry point"
```

---
