# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Running the Assistant
- **Standard run**: `uv run press-to-talk`
- **Text-only mode (skip STT/TTS)**: `uv run press-to-talk --text-input "your query" --no-tts`
- **Intent classification only**: `uv run press-to-talk --execution-mode database --classify-only --no-tts`
- **Regression testing with samples**: `uv run press-to-talk --intent-samples-file testdata/intent_samples.jsonl`

### Storage & Memory
- **Storage CLI**: `uv run ptt-storage`
- **Migrate legacy mem0 app ID** (legacy backend, 记忆主链路已切换为 Memos): `uv run python scripts/migrate_mem0_app_id.py --apply`

### Testing
- **Run all tests**: `uv run pytest`
- **Mem0 E2E tests**: `PTT_RUN_E2E=1 uv run python tests/mem0_e2e.py`

## Architecture & Structure

### High-Level Flow
The assistant follows a linear pipeline:
`Audio Input` $\rightarrow$ `STT` $\rightarrow$ `Intent Extraction` $\rightarrow$ `Context Retrieval (Memory)` $\rightarrow$ `LLM Summary` $\rightarrow$ `TTS Output`

### Core Components
- `press_to_talk/`: Main Python package.
  - `cli.py`: CLI entry point and command orchestration.
  - `storage/`: Memory and history management.
- `workflow_config.json`: Central configuration for the assistant's logic, including storage providers and model settings.
- `data/`: Local storage for logs and cache.

### Memory System
The project's memory read/write chain is backed by **Memos**（自托管 REST，`scripts/memo_api.py` 与 fast-path 直连 `ds.home:5230`）。`storage.provider` 下还保留两个 legacy 后端：
1. **`pocketbase`**: Legacy local/remote backend for memory and history.
2. **`mem0`**: Legacy external memory service（`/v1/memories` 遗留接口仍读取，可逐步剥离）。

The "remember" functionality (adding/finding memories) typically delegates to an external script defined by `URSOFT_REMEMBER_SCRIPT`.

### Execution Modes
- `memory-chat` (Default): Retrieves relevant memories as context and uses an LLM to generate a response.
- `database`: Strictly queries the internal database without LLM fallback.
- `hermes`: Delegates processing to an external `hermes chat` agent.

## 强制要求：TTS 语音总结

**每次回复结束时，必须在末尾输出一段便于 tts 工具读的总结（不超过 30 字），用 `<tts></tts>` 包裹。**

例如：`<tts>已修复完成，测试通过！</tts>`

- 这是强制要求，不是建议！
- 确保标签成对出现，不要丢失闭合标签。
- Hook 脚本会从 transcript JSON 中自动提取最后一对 `<tts>...</tts>` 内容用于语音播报。
