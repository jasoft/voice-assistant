# 集成服务 (INTEGRATIONS.md)

## AI 与 模型服务
- **OpenAI API**: 用于实时语音转文字 (STT) 和核心推理 (LLM)。
- **Mem0**: 用于实现长期记忆管理，支持用户偏好和上下文持久化。
- **Qwen TTS**: 阿里通义千问语音合成系统，用于生成最后的语音回复。

## 工具与协议
- **Model Context Protocol (MCP)**: 集成了 `brave-search` 和 `fetch` 等工具，增强 LLM 的搜索和网页抓取能力。
- **Tunelo**: 用于 Web 端访问的隧道内网穿透。

## 基础设施
- **SQLite (FTS5)**: 用于本地高效的全文搜索。
- **PocketBase (可选)**: 支持远程/分布式存储后端。
- **Mac CLI Utilities**: 利用系统自带的 `afplay` 播放提示音。
