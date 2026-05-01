# 代码约定 (CONVENTIONS.md)

## 日志输出
- **标准日志**: 使用 `press_to_talk.utils.logging:log`，支持图标 (✨, ⚠️, ❌) 和彩色输出。
- **Stderr 优先**: 所有日志必须重定向到 `stderr`，确保 `stdout` 仅用于干净的数据流或事件传递。
- **智能格式化**: 如果日志内容是 JSON，系统会尝试调用 `jq` 进行彩色格式化显示。
- **Session 日志**: 自动将当前会话的所有日志持久化到 `logs/` 目录下的 `.log` 文件。

## 逻辑实现
- **行为树节点**: 新的业务逻辑应实现为 `press_to_talk.execution.bt.nodes:Action` 或 `Condition`。
- **状态管理**: 统一使用 `Blackboard` 对象，严禁在节点外维护全局业务状态。

## 环境配置
- **变量驱动**: 优先从 `.env` 或环境变量读取配置（如 `PTT_LOG_LEVEL`, `PTT_VERBOSE`）。
- **外部提示词**: 所有的提示词必须放在 `workflow_config.json` 等外部 JSON 文件中，严禁硬编码。
