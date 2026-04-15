# Storage Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 storage 重构成清晰的配置、repository、CLI app、wrapper、facade 分层，同时保持现有接口与行为兼容。

**Architecture:** 保留 `StorageService` 作为对外入口，内部把 history/memory 的 SQLite CRUD、LLM 改写器、CLI 协议拆到独立模块。CLI 继续作为主程序与存储层的稳定边界。

**Tech Stack:** Python, sqlite3, argparse, unittest, subprocess

---

### Task 1: 拆出配置与模型

**Files:**
- Create: `docs/superpowers/specs/2026-04-16-storage-boundaries-design.md`
- Create: `docs/superpowers/plans/2026-04-16-storage-boundaries.md`
- Create: `press_to_talk/storage/config.py`
- Create: `press_to_talk/storage/models.py`
- Modify: `press_to_talk/storage/__init__.py`
- Modify: `press_to_talk/storage/service.py`
- Test: `tests/test_core_behaviors.py`

- [ ] 提取 `StorageConfig` 与配置加载函数。
- [ ] 提取 record dataclass 与 store protocol/base。
- [ ] 在 `service.py` 保留兼容导出，避免外部调用点回归。

### Task 2: 拆出 history/memory repository

**Files:**
- Create: `press_to_talk/storage/sqlite_history.py`
- Create: `press_to_talk/storage/sqlite_memory.py`
- Create: `press_to_talk/storage/text_rewrite.py`
- Modify: `press_to_talk/storage/service.py`
- Test: `tests/test_core_behaviors.py`

- [ ] 把 history CRUD 移到独立 repository。
- [ ] 把 memory CRUD/FTS/filter 移到独立 repository。
- [ ] 把 LLM keyword rewrite / memory translate 从 repository 文件中拆出。
- [ ] 保持 `StorageService.remember_store()/history_store()/keyword_rewriter()` 行为一致。

### Task 3: 拆出 CLI app 与 wrapper 协议

**Files:**
- Create: `press_to_talk/storage/cli_app.py`
- Modify: `press_to_talk/storage_cli.py`
- Modify: `press_to_talk/storage/cli_wrapper.py`
- Test: `tests/test_core_behaviors.py`, `tests/test_gui_events.py`

- [ ] 把 argparse 和命令调度从 `storage_cli.py` 移到 `cli_app.py`。
- [ ] 保持 `memory search` 的 `stdout`/`stderr` 协议不变。
- [ ] 简化 wrapper，让 history/memory 共享稳定的子进程执行逻辑。

### Task 4: 回归与验收

**Files:**
- Modify: `tests/test_core_behaviors.py`
- Modify: `tests/test_gui_events.py`

- [ ] 跑 storage 相关单测。
- [ ] 跑 `uv run python -m press_to_talk.storage_cli memory search --query '"usb" OR "测试版"'`。
- [ ] 跑 `uv run press-to-talk --text-input "usb测试版在哪" --no-tts`。
- [ ] 提交一次原子 commit。
