# 任务 5：迁移与入口点修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 API 添加运行入口并增强数据库迁移脚本的安全性和原子性。

**Architecture:** 
- 在 API 模块中使用 `uvicorn.run` 实现启动函数。
- 在迁移脚本中使用 `shutil.copy2` 进行备份，并利用 Peewee 的 `db.atomic()` 确保事务性。

**Tech Stack:** Python, FastAPI, Uvicorn, Peewee, shutil

---

### Task 1: 为 API 添加 `run_server` 入口点

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 在 `press_to_talk/api/main.py` 中添加 `run_server` 函数**

```python
def run_server():
    """Entry point for ptt-api command."""
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="Run the Press-to-Talk API server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind the server to.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind the server to.")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload.")
    
    args = parser.parse_args()
    
    # We use the string import pattern to allow reload to work correctly
    uvicorn.run("press_to_talk.api.main:app", host=args.host, port=args.port, reload=args.reload)
```

- [ ] **Step 2: 验证 `ptt-api --help` 命令**

Run: `ptt-api --help`
Expected: 显示帮助信息，且没有 "AttributeError: module 'press_to_talk.api.main' has no attribute 'run_server'" 错误。

- [ ] **Step 3: Commit**

```bash
git add press_to_talk/api/main.py
git commit -m "fix: add run_server entry point to api module"
```

---

### Task 2: 增强迁移脚本的安全性

**Files:**
- Modify: `scripts/migrate_v2_peewee.py`

- [ ] **Step 1: 添加备份逻辑和事务支持**

```python
import shutil
import datetime

# ... (在 migrate 函数开头添加)
def migrate():
    db_path = DEFAULT_APP_DB_PATH.expanduser()
    if not db_path.exists():
        print(f"Database not found at {db_path}, skipping migration.")
        return

    # Backup the database
    backup_path = db_path.with_suffix(f".bak.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}")
    print(f"Backing up database to {backup_path}...")
    shutil.copy2(db_path, backup_path)

    print(f"Migrating database at {db_path}...")
    db.init(str(db_path))
    db.connect()

    try:
        with db.atomic():
            # ... (将现有的迁移逻辑移入此 block)
            # Ensure tables exist
            db.create_tables([APIToken, SessionHistory, RememberEntry])
            # ...
    except Exception as e:
        print(f"Migration failed: {e}")
        # db.atomic() handles rollback automatically on exception
        raise
    finally:
        db.close()
```

- [ ] **Step 2: 运行迁移脚本验证（冒烟测试）**

Run: `python3 scripts/migrate_v2_peewee.py`
Expected: 即使已经迁移过，脚本也应该正常运行并生成备份文件。

- [ ] **Step 3: Commit**

```bash
git add scripts/migrate_v2_peewee.py
git commit -m "fix: add database backup and transactionality to migration script"
```
