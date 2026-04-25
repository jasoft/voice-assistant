# Task 5: Migration and Entry Points Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate existing SQLite database to the new multi-user schema and add API entry points.

**Architecture:** Use Peewee models and manual SQL to update existing records to a default user 'soj', and add the necessary CLI entry point for the API server.

**Tech Stack:** Python, Peewee, SQLite, UV.

---

### Task 1: Create migration script

**Files:**
- Create: `scripts/migrate_v2_peewee.py`

- [ ] **Step 1: Write the migration script**

```python
import sys
import os
from pathlib import Path

# Add project root to sys.path
APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(APP_ROOT))

from press_to_talk.storage.models import db, APIToken, SessionHistory, RememberEntry
from press_to_talk.storage.service import DEFAULT_APP_DB_PATH
from press_to_talk.utils.logging import log

def migrate():
    db_path = DEFAULT_APP_DB_PATH.expanduser()
    if not db_path.exists():
        print(f"Database not found at {db_path}, skipping migration.")
        return

    print(f"Migrating database at {db_path}...")
    db.init(str(db_path))
    db.connect()

    # Ensure tables exist
    db.create_tables([APIToken, SessionHistory, RememberEntry])

    # 1. Update session_histories
    updated_sessions = SessionHistory.update(user_id='soj').where(
        (SessionHistory.user_id == None) | (SessionHistory.user_id == '')
    ).execute()
    print(f"Updated {updated_sessions} session history records.")

    # 2. Update remember_entries
    updated_remember = RememberEntry.update(user_id='soj').where(
        (RememberEntry.user_id == None) | (RememberEntry.user_id == '')
    ).execute()
    print(f"Updated {updated_remember} remember entries.")

    # 3. Handle FTS and Embeddings tables (manual SQL)
    conn = db.connection()
    
    # Check remember_entries_simple_fts
    try:
        conn.execute("UPDATE remember_entries_simple_fts SET user_id = 'soj' WHERE user_id IS NULL OR user_id = ''")
        print("Updated FTS table.")
    except Exception as e:
        print(f"FTS table update skipped or failed: {e}")

    # Check remember_entry_embeddings
    try:
        # Check if user_id column exists
        cursor = conn.execute("PRAGMA table_info(remember_entry_embeddings)")
        columns = [row[1] for row in cursor.fetchall()]
        if "user_id" not in columns:
            conn.execute("ALTER TABLE remember_entry_embeddings ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
            print("Added user_id column to embeddings table.")
        
        conn.execute("UPDATE remember_entry_embeddings SET user_id = 'soj' WHERE user_id IS NULL OR user_id = ''")
        print("Updated embeddings table.")
    except Exception as e:
        print(f"Embeddings table update skipped or failed: {e}")

    # 4. Create default API token
    token_val = 'soj-default-token'
    if not APIToken.select().where(APIToken.token == token_val).exists():
        APIToken.create(
            token=token_val,
            user_id='soj',
            description='Default token for soj'
        )
        print(f"Created default API token: {token_val}")
    else:
        print(f"API token {token_val} already exists.")

    db.close()
    print("Migration completed successfully.")

if __name__ == "__main__":
    migrate()
```

- [ ] **Step 2: Commit migration script**

```bash
git add scripts/migrate_v2_peewee.py
git commit -m "feat: add migration script for v2 multi-user system"
```

### Task 2: Add API entry point to `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add ptt-api script**

Add `ptt-api = "press_to_talk.api.main:run_server"` under `[project.scripts]`.

- [ ] **Step 2: Commit pyproject.toml**

```bash
git add pyproject.toml
git commit -m "chore: add ptt-api entry point"
```

### Task 3: Run migration and verify

- [ ] **Step 1: Run the migration**

Run: `uv run python3 scripts/migrate_v2_peewee.py`
Expected: Output showing records updated and token created.

- [ ] **Step 2: Verify API server can start**

Run: `uv run ptt-api --help`
Expected: Help message for the API server.

- [ ] **Step 3: Smoke test API server**

Run: `uv run ptt-api` in background, wait a few seconds, then `curl http://127.0.0.1:8000/health`.
Expected: `{"status":"ok"}`.

- [ ] **Step 4: Commit any fixes**

```bash
git add .
git commit -m "fix: final adjustments after migration and entry point testing" || true
```
