<!-- 项目约束引用 -->
<!-- Import: AGENTS.md -->

# Gemini CLI 运行指南

本项目已在 `AGENTS.md` 中定义了核心硬约束，请务必严格遵守。
任何修改前，必须阅读 `@docs/system_architecture.md` 以确保理解行为树执行引擎与存储层隔离架构。

## 关键同步

- 每次修改代码后，请确保执行 `AGENTS.md` 中要求的收尾校验。
- GUI 修改必须使用 release 模式编译。
- 执行层逻辑改动必须在行为树框架内完成。
