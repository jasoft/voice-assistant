# Press-to-Talk Voice Assistant

## What This Is

一个专为 macOS 设计的极速本地语音助手。它通过“按下即说”的交互模式，集成了 STT、行为树逻辑引擎、MCP 工具调用以及长短期记忆管理，为用户提供丝滑的系统级自动化与信息检索体验。

## Core Value

**启动即录音，响应无延迟。** 在不破坏录音体验的前提下，提供最智能的执行与反馈。

## Requirements

### Validated

- ✓ **全链路语音闭环** — 包含录音、转文字、意图识别、执行、播报。
- ✓ **行为树架构** — 采用 BT 模式替代嵌套 if-else，逻辑清晰可维护。
- ✓ **进程级存储隔离** — 核心逻辑与存储层解耦，支持 SQLite/PocketBase。
- ✓ **多端 GUI 支持** — 包含 Swift 状态栏应用及 Web 管理界面。
- ✓ **MCP 工具集成** — 支持 Brave Search 和 Fetch 等扩展能力。

### Active

- [ ] **GSD 工作流整合** — 建立标准的 Phase、Plan、Review 闭环。
- [ ] **Skill 体系标准化** — 修复并激活所有内置技能（如 ptt-voice, remember）。
- [ ] **首字延迟优化** — 进一步缩短 LLM 处理和流式响应的时间。

### Out of Scope

- **重型 GUI 交互** — 坚持“按下即说”的轻量化交互，不开发复杂的桌面窗口程序。
- **云端强依赖** — 目标是尽可能本地化，不接受必须联网才能运行的核心链路（STT/TTS 优先本地化）。

## Context

- **开发环境**: macOS (M5 芯片), Python 3.13+, Swift。
- **技术渊源**: 从传统的简单录音脚本演进为基于 AI 的智能助手。
- **用户反馈**: 大王要求极高的响应速度和工程质量。

## Constraints

- **性能**: 录音前严禁任何阻塞。
- **架构**: 必须遵循行为树规范。
- **配置**: 提示词严禁硬编码。

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| 行为树架构 | 解决复杂意图切换时的逻辑混乱问题 | ✓ Good |
| 存储隔离 | 提高数据安全性，便于未来支持多后端 | ✓ Good |
| Swift Mac GUI | 提供原生的系统级集成体验 | ✓ Good |

---
*Last updated: 2026-05-02 after /gsd-map-codebase*
