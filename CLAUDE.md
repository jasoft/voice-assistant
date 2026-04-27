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
- **Migrate mem0 app ID**: `uv run python scripts/migrate_mem0_app_id.py --apply`

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
- `data/`: Local storage for SQLite databases (history and FTS memory).

### Memory System
The project supports two primary memory backends, configured in `workflow_config.json` under `storage.provider`:
1. **`sqlite_fts5`**: Local SQLite using Full Text Search. Can optionally use an LLM to rewrite queries into keywords (`groq_query_rewrite`).
2. **`mem0`**: External memory service.

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
- Hook 脚本会自动提取最后一对 `<tts>...</tts>` 内容用于语音播报。
- **重要**：输出 `<tts>总结</tts>` 后，必须立即执行：`echo "总结内容" > /tmp/claude_last_tts.txt`
  - 这样 stop hook 能立即读到总结，无需等待 transcript 写入。
