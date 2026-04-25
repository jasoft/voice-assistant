# Refactor Storage Layer (Critical Fixes) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix table name mismatches, resolve FTS concurrency risks, and ensure FTS table isolation by user_id.

**Architecture:** Use Peewee's `table_name` Meta attribute for explicit schema mapping. Update FTS5 virtual table schema to include `user_id` and modify synchronization logic to be incremental and isolated.

**Tech Stack:** Python, Peewee ORM, SQLite FTS5.

---

### Task 1: Fix Table Names in Models

**Files:**
- Modify: `press_to_talk/storage/models.py`

- [ ] **Step 1: Set explicit table names for Models**

```python
class APIToken(BaseModel):
    # ...
    class Meta:
        table_name = 'api_tokens'

class SessionHistory(BaseModel):
    # ...
    class Meta:
        table_name = 'session_histories'

class RememberEntry(BaseModel):
    # ...
    class Meta:
        table_name = 'remember_entries'
```

- [ ] **Step 2: Verify Peewee recognizes the new table names**
(Manual check or simple script if needed, but this is standard Peewee behavior).

---

### Task 2: Update FTS Table Schema and Sync Logic

**Files:**
- Modify: `press_to_talk/storage/providers/sqlite_fts.py`

- [ ] **Step 1: Update `_connect` to include `user_id` in FTS table and remove full sync**

```python
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {self.fts_table_name}
            USING fts5(
                memory,
                original_text,
                user_id UNINDEXED,
                item_id UNINDEXED
                {fts_tokenizer_clause}
            )
            """
        )
        # REMOVE the DELETE and full INSERT logic
```

- [ ] **Step 2: Update `add` method to include `user_id` in FTS insert**

```python
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    f"""
                    INSERT INTO {self.fts_table_name} (
                        memory,
                        original_text,
                        user_id,
                        item_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (stored_memory, stored_original_text, self.user_id, item_id),
                )
```

- [ ] **Step 3: Update `update` method to include `user_id` in FTS insert**

```python
                conn.execute(
                    f"""
                    INSERT INTO {self.fts_table_name} (
                        memory,
                        original_text,
                        user_id,
                        item_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (stored_memory, stored_original_text, self.user_id, memory_id),
                )
```

- [ ] **Step 4: Update `delete` method to use `user_id` when deleting from FTS**

```python
                conn.execute(
                    f"DELETE FROM {self.fts_table_name} WHERE item_id = ? AND user_id = ?",
                    (memory_id, self.user_id),
                )
```

---

### Task 3: Update Search SQL for Isolation

**Files:**
- Modify: `press_to_talk/storage/providers/sqlite_fts.py`

- [ ] **Step 1: Update `find` method's FTS query**

```python
            rows = conn.execute(
                f"""
                SELECT
                    items.id,
                    items.memory,
                    items.original_text,
                    items.created_at,
                    items.updated_at
                FROM {self.fts_table_name} fts
                JOIN {self.table_name} items ON items.id = fts.item_id
                WHERE fts.user_id = ? AND {self.fts_table_name} MATCH {match_sql}
                ORDER BY bm25({self.fts_table_name}), items.updated_at DESC
                LIMIT ?
                """,
                (self.user_id, match_query, self.max_results),
            ).fetchall()
```

---

### Task 4: Verification

- [ ] **Step 1: Run isolation reproduction script**
Run: `python reproduce_fts_isolation.py` (Create this script to verify user A doesn't see user B's FTS results).

- [ ] **Step 2: Run existing tests**
Run: `pytest tests/test_smoke_check.py`

- [ ] **Step 3: Commit changes**

```bash
git add press_to_talk/storage/models.py press_to_talk/storage/providers/sqlite_fts.py
git commit -m "fix(storage): resolve table name mismatch, FTS concurrency and isolation issues"
```
