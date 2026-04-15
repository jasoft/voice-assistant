# Storage CLI Refactor - Task 4: Integration and Decoupling

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the main application to use CLI-based storage while decoupling LLM logic from the storage layer.

**Architecture:**
- `StorageService` acts as a switcher between CLI wrappers (for main app) and real stores (for CLI).
- LLM-based query rewriting is performed at the Agent level.
- Storage layer remains focused on raw retrieval and persistence.

**Tech Stack:** Python, subprocess, SQLite FTS5.

---

### Task 4.1: Update StorageService for Dual Mode

**Files:**
- Modify: `press_to_talk/storage/service.py`

- [ ] **Step 1: Update StorageService.__init__ to support direct and CLI modes**

```python
    def __init__(self, config: StorageConfig, use_cli: bool = True) -> None:
        self.config = StorageConfig(
            backend=config.backend,
            mem0_api_key=config.mem0_api_key,
            mem0_user_id=config.mem0_user_id,
            mem0_app_id=config.mem0_app_id,
            mem0_min_score=config.mem0_min_score,
            mem0_max_items=config.mem0_max_items,
            history_db_path=config.history_db_path,
            remember_db_path=config.remember_db_path,
            remember_max_results=config.remember_max_results,
            query_rewrite_enabled=config.query_rewrite_enabled,
            llm_api_key=config.llm_api_key,
            llm_base_url=config.llm_base_url,
            llm_model=config.llm_model,
        )
        if use_cli:
            from .cli_wrapper import CLIHistoryStore, CLIRememberStore
            self._history_store = CLIHistoryStore()
            self._remember_store = CLIRememberStore()
        else:
            # History
            self._history_store = SQLiteHistoryStore(self.config.history_db_path)
            
            # Remember
            if self.config.backend == "sqlite_fts5":
                self._remember_store = SQLiteFTS5RememberStore(
                    db_path=self.config.remember_db_path,
                    max_results=self.config.remember_max_results,
                    keyword_rewriter=self.keyword_rewriter(),
                )
            else:
                self._remember_store = Mem0RememberStore(
                    api_key=self.config.mem0_api_key,
                    user_id=self.config.mem0_user_id,
                    app_id=self.config.mem0_app_id,
                )
```

- [ ] **Step 2: Update from_env classmethod to accept use_cli**

```python
    @classmethod
    def from_env(cls, use_cli: bool = True) -> "StorageService":
        return cls(load_storage_config(), use_cli=use_cli)
```

- [ ] **Step 3: Modify SQLiteFTS5RememberStore._match_query to skip rewrite if query looks already processed**

```python
    def _match_query(self, query: str) -> str:
        cleaned_query = str(query or "").strip()
        if not cleaned_query:
            return ""

        # If the query already looks like a FTS5 match query (contains OR or quotes), use it directly
        if ' OR ' in cleaned_query or (cleaned_query.startswith('"') and cleaned_query.endswith('"')):
            log(f"Query already processed: {cleaned_query}")
            return cleaned_query

        # ... (rest of the logic)
```

### Task 4.2: Update Storage CLI to use Direct Mode

**Files:**
- Modify: `press_to_talk/storage_cli.py`

- [ ] **Step 1: Update main() to initialize StorageService with use_cli=False**

```python
    try:
        # Pass use_cli=False to avoid infinite recursion
        service = StorageService.from_env(use_cli=False)
        # Force disable LLM features in CLI
        service.config.query_rewrite_enabled = False
```

### Task 4.3: Verify Agent Decoupling

**Files:**
- Modify: `press_to_talk/agent/agent.py` (Verify only, or fix if needed)

- [ ] **Step 1: Ensure _execute_remember_tool performs rewrite**
Check lines ~410-425 in `agent.py`. It should look like this:
```python
            elif name == "remember_find":
                query = str(args.get("query", ""))
                rewriter = self.storage.keyword_rewriter()
                if rewriter:
                    try:
                        rewritten_query = rewriter.rewrite(query)
                        if rewritten_query:
                            log(f"Query rewritten: '{query}' -> '{rewritten_query}'")
                            query = rewritten_query
                    except Exception as e:
                        log(f"Query rewrite failed in agent: {e}")
                output = remember_store.find(query=query)
```

### Task 4.4: Final Verification and Commit

- [ ] **Step 1: Run application and check logs**
Run: `uv run ptt_voice.py` (or equivalent)
Action: Speak a query that should trigger a memory search.
Check: Logs should show `Query rewritten` and `remember tool request`.
Verify: `Storage CLI` is used (no direct SQLite logs from the main process for storage).

- [ ] **Step 2: Commit changes**

```bash
git add press_to_talk/storage/service.py press_to_talk/storage_cli.py press_to_talk/agent/agent.py
git commit -m "refactor: switch main app to use CLI-based storage and decouple LLM logic"
```
