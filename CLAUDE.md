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

## 总结

- 在最后的总结里，在末尾输出一段便于 tts 工具读的总结，不多于 30 字。用 `<tts></tts>` 包裹起来，例如 `<tts>我的任务完成了, 已经解决了不能写入的问题!</tts>`
- 确保 TTS tag 是成对出现，不要出现丢失。
