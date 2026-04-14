# SQLite FTS5 Memory Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 remember 数据层独立成可配置 provider，并新增基于 SQLite FTS5 的本地存储与全文检索，同时在查询前用 Groq 做关键词拆分增强。

**Architecture:** 保持上层 `remember_add` / `remember_find` 调用接口不变，在 `StorageService` 内部通过配置选择 provider。新增 `SQLiteFTS5RememberStore` 负责本地落库与 FTS 检索，查询前可选调用 OpenAI 兼容客户端把原始问题改写成 FTS 友好的关键词表达式，失败时降级为原始 query。

**Tech Stack:** Python 3.13, sqlite3 FTS5, OpenAI-compatible Groq client, unittest

---

### Task 1: 配置与 provider 边界

**Files:**
- Modify: `press_to_talk/storage/service.py`
- Modify: `press_to_talk/models/history.py`
- Modify: `workflow_config.json`
- Test: `tests/test_core_behaviors.py`

- [ ] 写失败测试，覆盖配置文件可切换 remember provider、sqlite 路径和查询增强配置
- [ ] 运行目标测试确认失败点正确
- [ ] 实现 `StorageConfig` 扩展、配置解析与 provider 工厂
- [ ] 运行目标测试确认通过
- [ ] 提交一次

### Task 2: SQLite FTS5 provider

**Files:**
- Modify: `press_to_talk/storage/service.py`
- Test: `tests/test_core_behaviors.py`

- [ ] 写失败测试，覆盖 sqlite provider 同时保存 `memory` / `original_text`、可按 FTS 命中返回结果
- [ ] 运行目标测试确认失败点正确
- [ ] 实现 SQLite 主表、FTS5 虚表、写入与查询格式化
- [ ] 运行目标测试确认通过
- [ ] 提交一次

### Task 3: Groq 查询增强与回归

**Files:**
- Modify: `press_to_talk/storage/service.py`
- Modify: `tests/test_core_behaviors.py`
- Modify: `README.md`

- [ ] 写失败测试，覆盖查询前关键词拆分、失败降级与保持上层 remember 接口不变
- [ ] 运行目标测试确认失败点正确
- [ ] 实现查询增强逻辑并接入 sqlite provider
- [ ] 跑完整相关测试与文档更新
- [ ] 提交一次
