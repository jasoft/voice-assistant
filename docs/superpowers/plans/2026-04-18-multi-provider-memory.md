# Multi-Provider Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `mem0` 和本地 `sqlite_fts5 + embedding` 记忆检索拆成可切换的多 provider，并移除 agent 对 `mem0` 专用总结逻辑的耦合。

**Architecture:** `StorageService` 负责按配置装配 provider，provider 自己负责 `find()` 与 `extract_summary_items()`。agent 只消费统一的 `items` 结构，不再直接知道 `mem0` 阈值规则。本地 CLI wrapper 通过委托当前 provider 的 summary extractor 保持协议不变。

**Tech Stack:** Python, unittest, SQLite FTS5, LM Studio embedding, local storage CLI

---

### Task 1: Provider 接口接线

**Files:**
- Create: `press_to_talk/storage/providers/__init__.py`
- Create: `press_to_talk/storage/providers/mem0.py`
- Create: `press_to_talk/storage/providers/sqlite_fts.py`
- Modify: `press_to_talk/storage/models.py`
- Modify: `press_to_talk/storage/service.py`
- Modify: `press_to_talk/storage/cli_wrapper.py`

- [ ] 给 `BaseRememberStore` 增加 `extract_summary_items()` 接口
- [ ] 为 `mem0` 和 `sqlite_fts5` 提供各自的 summary extractor
- [ ] 让 `StorageService` 只负责 provider 选择，不再把 provider 细节泄露给 agent
- [ ] 让 `CLIRememberStore` 委托当前 provider 做 summary item 提取

### Task 2: Agent 切走 mem0 专用逻辑

**Files:**
- Modify: `press_to_talk/agent/agent.py`
- Modify: `press_to_talk/agent/memory.py`
- Modify: `press_to_talk/core.py`

- [ ] 把 agent 的 remember summary 路径改成调用 `self.storage.remember_store().extract_summary_items(...)`
- [ ] 保留 `extract_mem0_summary_payload()` 的兼容导出，避免现有 mem0 单测断裂
- [ ] 确保本地 SQLite provider 不再读 `mem0.min_score`

### Task 3: 回归测试与验证

**Files:**
- Modify: `tests/test_core_behaviors.py`

- [ ] 给 `FakeRememberStore` 增加 `extract_summary_items()`
- [ ] 补一条 sqlite provider 的 agent 回归测试，覆盖“查到了拔牙记录但被 mem0 阈值误判为空”的场景
- [ ] 跑 targeted unittest
- [ ] 跑 `uv run press-to-talk --text-input "usb测试版在哪" --no-tts`
- [ ] 完成后提交本轮改动
