# Storage CLI Refactor Design

## Goal
Decouple storage logic into a standalone CLI tool for third-party use and refactor the main application to use this CLI via process isolation (`subprocess`). Ensure the storage layer is "pure" and contains no LLM calls.

## Architecture

### 1. Pure Storage CLI (`press_to_talk/storage_cli.py`)
- **Commands**:
    - `history add`: Save a session record.
    - `history list`: List recent sessions (JSON).
    - `history delete`: Delete a session by ID.
    - `memory add`: Save a long-term memory.
    - `memory search`: Search memories using FTS5/Mem0 (Pure search, no LLM rewrite).
    - `memory list`: List all memories.
    - `memory delete`: Delete a memory by ID.
- **Rules**:
    - Input/Output MUST be JSON.
    - LLM features (query rewrite, translation) MUST be disabled.
    - Use `StorageService` internally but with `query_rewrite_enabled=False`.

### 2. CLI Wrapper (`press_to_talk/storage/cli_wrapper.py`)
- A new module that encapsulates `subprocess` calls to the CLI.
- Implements `BaseHistoryStore` and `BaseRememberStore` interfaces.
- Handles Python-to-JSON serialization and error handling for subprocess calls.

### 3. Application Layer
- The main app (e.g., `core.py`) continues to use `StorageService`.
- `StorageService` will be updated to return the `CLIWrapper` versions of stores.
- LLM tasks like "query rewriting" or "memory translation" are performed in the app layer *before* calling the storage layer.

## Data Flow
1. **Save**: `App` -> `CLIWrapper` -> `subprocess(storage_cli add)` -> `SQLite/Mem0`
2. **Search**: `App` -> `LLM (Rewrite Query)` -> `CLIWrapper` -> `subprocess(storage_cli search --query "rewritten")` -> `Results (JSON)`

## Technical Details

### SQLiteFTS5RememberStore Updates
- Add `delete(memory_id: str)` method.
- Add `list_all(limit: int)` method.

### CLI Enhancements
- Use `argparse` subparsers for clean command structure.
- Ensure proper error exit codes and stderr reporting.

## Testing Strategy
1. **CLI Tests**: Verify each CLI command independently with shell scripts.
2. **Integration Tests**: Verify the `CLIWrapper` correctly communicates with the CLI.
3. **End-to-End**: Ensure the voice assistant still saves and retrieves history/memory correctly.
