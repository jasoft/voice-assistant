# 技术栈 (STACK.md)

## 核心语言
- **Python**: 3.13+ (主要逻辑、API、CLI)
- **Swift**: 用于 Mac 本地 GUI (`mac_gui`)
- **JavaScript/HTML**: 用于 Web GUI (`web_gui`)

## 依赖管理
- **uv**: 用于 Python 包管理和虚拟环境同步
- **setuptools**: 构建系统

## 关键 Python 库
- **音频处理**: `sounddevice`, `numpy`
- **AI/LLM**: `openai` (STT/LLM), `mem0ai` (长期记忆)
- **UI/交互**: `rich` (终端 UI), `pynput` (全局热键监听)
- **Web 框架**: `fastapi`, `uvicorn`
- **数据库/ORM**: `peewee` (SQLite), `sqlite-web`
- **工具链**: `mcp` (Model Context Protocol), `httpx`, `python-multipart`

## 开发与测试
- **测试框架**: `pytest`
- **任务运行器**: `poethepoet` (定义了 `web`, `ptt`, `voice`, `doctor`, `api` 等快捷命令)
- **日志**: `vibe-colored-logger`

## 外部工具
- **TTS**: `qwen-tts` (通过命令行调用)
- **内网穿透**: `tunelo`
