# Storage CLI Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple storage logic into a standalone CLI tool and use process isolation (`subprocess`) for integration.

**Architecture:** 
- Standalone CLI for all CRUD operations on history and memories.
- Subprocess-based wrapper in the main app.
- LLM logic (rewrite/translate) moved out of the storage layer.

**Tech Stack:** Python, SQLite FTS5, argparse, subprocess.

---

### Task 1: Complete SQLite Store Interface

**Files:**
- Modify: `press_to_talk/storage/service.py`

- [ ] **Step 1: Add delete and list_all to BaseRememberStore**

```python
class BaseRememberStore:
    def add(self, *, memory: str, original_text: str = "") -> str:
        raise NotImplementedError

    def find(self, *, query: str) -> str:
        raise NotImplementedError

    def delete(self, *, memory_id: str) -> None:
        raise NotImplementedError

    def list_all(self, *, limit: int = 100) -> list[RememberItemRecord]:
        raise NotImplementedError
```

- [ ] **Step 2: Implement delete and list_all in SQLiteFTS5RememberStore**

```python
    def delete(self, *, memory_id: str) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(f"DELETE FROM {self.fts_table_name} WHERE item_id = ?", (memory_id.strip(),))
                conn.execute(f"DELETE FROM {self.table_name} WHERE id = ?", (memory_id.strip(),))

    def list_all(self, *, limit: int = 100) -> list[RememberItemRecord]:
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT id, source_memory_id, memory, original_text, created_at FROM {self.table_name} ORDER BY created_at DESC LIMIT ?",
                (max(1, limit),)
            ).fetchall()
        return [
            RememberItemRecord(
                id=str(row["id"]),
                source_memory_id=str(row["source_memory_id"]),
                memory=str(row["memory"]),
                original_text=str(row["original_text"]),
                created_at=str(row["created_at"]),
            ) for row in rows
        ]
```

- [ ] **Step 3: Implement delete and list_all in Mem0RememberStore**

```python
    def delete(self, *, memory_id: str) -> None:
        self.client.delete(memory_id)

    def list_all(self, *, limit: int = 100) -> list[RememberItemRecord]:
        items = self.get_all()
        return [
            RememberItemRecord(
                id=item["id"],
                source_memory_id="",
                memory=item["memory"],
                original_text=item.get("metadata", {}).get("original_text", ""),
                created_at=item.get("created_at", "")
            ) for item in items[:limit]
        ]
```

- [ ] **Step 4: Commit changes**

```bash
git add press_to_talk/storage/service.py
git commit -m "feat: add delete and list_all to remember stores"
```

### Task 2: Refactor Storage CLI

**Files:**
- Modify: `press_to_talk/storage_cli.py`

- [ ] **Step 1: Rewrite storage_cli.py with subcommands**

```python
import argparse
import json
import sys
from dataclasses import asdict
from press_to_talk.storage.service import StorageService, SessionHistoryRecord

def main() -> int:
    parser = argparse.ArgumentParser(prog="press-to-talk-storage")
    subparsers = parser.add_subparsers(dest="category", required=True)

    # History category
    history_parser = subparsers.add_parser("history")
    history_sub = history_parser.add_subparsers(dest="command", required=True)
    
    # history list
    h_list = history_sub.add_parser("list")
    h_list.add_argument("--limit", type=int, default=10)
    h_list.add_argument("--query", default="")

    # history add
    h_add = history_sub.add_parser("add")
    h_add.add_argument("--json", required=True, help="SessionHistoryRecord as JSON string")

    # history delete
    h_del = history_sub.add_parser("delete")
    h_del.add_argument("--session-id", required=True)

    # Memory category
    memory_parser = subparsers.add_parser("memory")
    memory_sub = memory_parser.add_subparsers(dest="command", required=True)

    # memory add
    m_add = memory_sub.add_parser("add")
    m_add.add_argument("--memory", required=True)
    m_add.add_argument("--original-text", default="")

    # memory search
    m_search = memory_sub.add_parser("search")
    m_search.add_argument("--query", required=True)

    # memory delete
    m_del = memory_sub.add_parser("delete")
    m_del.add_argument("--id", required=True)

    # memory list
    m_list = memory_sub.add_parser("list")
    m_list.add_argument("--limit", type=int, default=100)

    args = parser.parse_args()
    
    from press_to_talk.core import load_env_files
    load_env_files()
    
    try:
        service = StorageService.from_env()
        # Force disable LLM features in CLI
        service.config.query_rewrite_enabled = False

        if args.category == "history":
            store = service.history_store()
            if args.command == "list":
                records = store.list_recent(limit=args.limit, query=args.query)
                print(json.dumps([asdict(r) for r in records], ensure_ascii=False))
            elif args.command == "add":
                data = json.loads(args.json)
                record = SessionHistoryRecord(**data)
                store.persist(record)
                print(json.dumps({"status": "ok"}))
            elif args.command == "delete":
                store.delete(session_id=args.session_id)
                print(json.dumps({"deleted": args.session_id}))

        elif args.category == "memory":
            store = service.remember_store()
            if args.command == "add":
                res = store.add(memory=args.memory, original_text=args.original_text)
                print(json.dumps({"result": res}))
            elif args.command == "search":
                res = store.find(query=args.query)
                print(res) # store.find already returns JSON string
            elif args.command == "delete":
                store.delete(memory_id=args.id)
                print(json.dumps({"deleted": args.id}))
            elif args.command == "list":
                records = store.list_all(limit=args.limit)
                print(json.dumps([asdict(r) for r in records], ensure_ascii=False))

        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
```

- [ ] **Step 2: Test CLI manually**

Run: `python -m press_to_talk.storage_cli history list --limit 1`
Expected: Valid JSON array.

- [ ] **Step 3: Commit**

```bash
git add press_to_talk/storage_cli.py
git commit -m "feat: enhance storage_cli with memory and full history support"
```

### Task 3: Create CLI Wrapper

**Files:**
- Create: `press_to_talk/storage/cli_wrapper.py`

- [ ] **Step 1: Implement CLIHistoryStore and CLIRememberStore**

```python
import json
import subprocess
import sys
from dataclasses import asdict
from typing import Any
from .service import BaseHistoryStore, BaseRememberStore, SessionHistoryRecord, RememberItemRecord

class CLIStoreBase:
    def _run(self, args: list[str]) -> Any:
        cmd = [sys.executable, "-m", "press_to_talk.storage_cli"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(f"Storage CLI error: {result.stderr}")
        return json.loads(result.stdout)

class CLIHistoryStore(BaseHistoryStore, CLIStoreBase):
    def persist(self, entry: SessionHistoryRecord) -> None:
        self._run(["history", "add", "--json", json.dumps(asdict(entry))])

    def list_recent(self, *, limit: int = 10, query: str = "") -> list[SessionHistoryRecord]:
        data = self._run(["history", "list", "--limit", str(limit), "--query", query])
        return [SessionHistoryRecord(**item) for item in data]

    def delete(self, *, session_id: str) -> None:
        self._run(["history", "delete", "--session-id", session_id])

class CLIRememberStore(BaseRememberStore, CLIStoreBase):
    def add(self, *, memory: str, original_text: str = "") -> str:
        data = self._run(["memory", "add", "--memory", memory, "--original-text", original_text])
        return data["result"]

    def find(self, *, query: str) -> str:
        # Note: CLI search already returns JSON string
        cmd = [sys.executable, "-m", "press_to_talk.storage_cli", "memory", "search", "--query", query]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        return result.stdout.strip()

    def delete(self, *, memory_id: str) -> None:
        self._run(["memory", "delete", "--id", memory_id])

    def list_all(self, *, limit: int = 100) -> list[RememberItemRecord]:
        data = self._run(["memory", "list", "--limit", str(limit)])
        return [RememberItemRecord(**item) for item in data]
```

- [ ] **Step 2: Commit**

```bash
git add press_to_talk/storage/cli_wrapper.py
git commit -m "feat: add subprocess-based CLI storage wrappers"
```

### Task 4: Integrate and Decouple LLM

**Files:**
- Modify: `press_to_talk/storage/service.py`
- Modify: `press_to_talk/agent/agent.py` (or similar) to handle LLM tasks.

- [ ] **Step 1: Update StorageService to use CLI stores**

```python
from .cli_wrapper import CLIHistoryStore, CLIRememberStore

class StorageService:
    def __init__(self, config: StorageConfig) -> None:
        # ...
        self._history_store = CLIHistoryStore()
        self._remember_store = CLIRememberStore()

    def remember_store(self) -> BaseRememberStore:
        return self._remember_store

    def history_store(self) -> BaseHistoryStore:
        return self._history_store
```

- [ ] **Step 2: Ensure LLM logic is moved out of Store classes**
Check `press_to_talk/agent/memory.py` or wherever retrieval is called. Ensure it handles its own rewriter.

- [ ] **Step 3: Final verification**
Run the full app, trigger a voice command, and check if history is saved via `storage_cli`.
Check logs for "Storage CLI error".

- [ ] **Step 4: Commit and Cleanup**

```bash
git add press_to_talk/storage/service.py
git commit -m "refactor: switch main app to use CLI-based storage"
```
