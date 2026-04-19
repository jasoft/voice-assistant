# Voice Assistant Storage Skill

This skill provides direct, pure access to session history and long-term memory via the Standalone Storage CLI. It is a strictly functional layer for data persistence and retrieval.

## Core Commands

Use the installed console script: `ptt-storage`.
It emits JSON on success, so agents can pipe directly to `jq`.

### 1. Session History

Save and retrieve conversation logs.

- **List history**: `ptt-storage history list [--limit N] [--query "text"]`
- **Add history**: `ptt-storage history add --json '{"session_id": "...", ...}'`
- **Delete history**: `ptt-storage history delete --session-id <id>`

### 2. Long-term Memory

Store and search persistent data entries.

- **Add memory**: `ptt-storage memory add --memory "Fact/Content" --original-text "Source Context"`
- **Search memory**: `ptt-storage memory search --query "search keyword"`
    - Performs direct FTS5/Mem0 search. Use raw keywords.
- **List memories**: `ptt-storage memory list [--limit N]`
- **Delete memory**: `ptt-storage memory delete --id <uuid>`

## Guidelines for Agents

1. **Direct Action**: Treat these commands as primitive storage operations. Pass data exactly as it should be stored or queried.
2. **Reliability**: Always use the output JSON for subsequent logic. Success JSON is on `stdout`; failures return JSON on `stderr`.
3. **Parsing**: Prefer `jq` to extract fields instead of grepping raw text.
4. **Keyword Search**: For SQLite `simple_query`, pass keywords and let the storage layer split them; avoid depending on literal `OR` output formatting.

## Examples

**Search for a record about "passport":**
```bash
ptt-storage history list --query "passport" --limit 1
```

**Directly save a fact:**
```bash
ptt-storage memory add --memory "The passport is in the top drawer" --original-text "My passport? Oh, it's in the top drawer."
```

**Read memory text + date with `jq`:**
```bash
ptt-storage memory search --query "usb" | jq -r '.results[] | "\(.memory)\t\(.created_at)"'
```
