from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from press_to_talk.utils.logging import log, log_llm_prompt, log_multiline
from press_to_talk.utils.text import format_local_datetime

APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APP_DB_PATH = APP_ROOT / "data" / "voice_assistant_store.sqlite3"
DEFAULT_HISTORY_DB_PATH = DEFAULT_APP_DB_PATH
DEFAULT_REMEMBER_DB_PATH = DEFAULT_APP_DB_PATH
SIMPLE_EXTENSION_PATH = APP_ROOT / "third_party" / "simple" / "libsimple.dylib"
WORKFLOW_CONFIG_PATH = APP_ROOT / "workflow_config.json"


@dataclass
class StorageConfig:
    backend: str
    mem0_api_key: str
    mem0_user_id: str
    mem0_app_id: str
    mem0_min_score: float
    mem0_max_items: int
    history_db_path: str
    remember_db_path: str
    remember_max_results: int
    query_rewrite_enabled: bool
    llm_api_key: str
    llm_base_url: str
    llm_model: str  # 统一使用全局模型


@dataclass
class RememberItemRecord:
    id: str
    source_memory_id: str
    memory: str
    original_text: str
    created_at: str


@dataclass
class SessionHistoryRecord:
    session_id: str
    started_at: str
    ended_at: str
    transcript: str
    reply: str
    peak_level: float
    mean_level: float
    auto_closed: bool
    reopened_by_click: bool
    mode: str


class KeywordRewriter(Protocol):
    def rewrite(self, query: str) -> str: ...


class MemoryTranslator(Protocol):
    def translate(self, text: str) -> str: ...


def env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_workflow_config() -> dict[str, Any]:
    try:
        with WORKFLOW_CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"workflow config missing required section: {path}")
    return value


def _render_prompt_template(template: str, values: dict[str, str]) -> str:
    rendered = str(template or "")
    for key, value in values.items():
        rendered = rendered.replace(f"${{{key}}}", value)
    return rendered


def _workflow_storage_config() -> dict[str, Any]:
    workflow = load_workflow_config()
    storage = workflow.get("storage", {}) if isinstance(workflow, dict) else {}
    return storage if isinstance(storage, dict) else {}


def load_storage_config() -> StorageConfig:
    storage_cfg = _workflow_storage_config()
    
    # 提取子配置
    sqlite_cfg = storage_cfg.get("sqlite_fts5", {})
    sqlite_cfg = sqlite_cfg if isinstance(sqlite_cfg, dict) else {}
    mem0_cfg = storage_cfg.get("mem0", {})
    mem0_cfg = mem0_cfg if isinstance(mem0_cfg, dict) else {}
    # 修正：直接从 sqlite_fts5 下读取 query_rewrite，并兼容旧键
    rewrite_cfg = sqlite_cfg.get("query_rewrite", sqlite_cfg.get("groq_query_rewrite", {}))
    rewrite_cfg = rewrite_cfg if isinstance(rewrite_cfg, dict) else {}

    # 获取全局或特定后端的检索限制，默认为 20
    global_max_results = int(storage_cfg.get("max_results", 20))
    
    raw_app_id = os.environ.get("MEM0_APP_ID")
    app_id = "voice-assistant" if raw_app_id is None else str(raw_app_id).strip()

    configured_backend = str(storage_cfg.get("provider", "mem0")).strip() or "mem0"
    
    # 获取全局模型配置
    default_model = env_str("PTT_MODEL", "qwen/qwen3-32b")

    # 汇总最终配置
    config = StorageConfig(
        backend=str(env_str("PTT_REMEMBER_BACKEND", configured_backend)).strip()
        or configured_backend,
        
        mem0_api_key=env_str("MEM0_API_KEY", "").strip(),
        mem0_user_id=str(env_str("MEM0_USER_ID", "soj")).strip() or "soj",
        mem0_app_id=app_id,
        mem0_min_score=env_float("MEM0_MIN_SCORE", float(mem0_cfg.get("min_score", 0.8))),
        mem0_max_items=max(1, env_int("MEM0_MAX_ITEMS", int(mem0_cfg.get("max_items", global_max_results)))),
        
        history_db_path=env_str(
            "PTT_HISTORY_DB_PATH", str(DEFAULT_HISTORY_DB_PATH)
        ).strip()
        or str(DEFAULT_HISTORY_DB_PATH),
        
        remember_db_path=env_str("PTT_REMEMBER_DB_PATH", str(sqlite_cfg.get("db_path", str(DEFAULT_REMEMBER_DB_PATH)))).strip()
        or str(DEFAULT_REMEMBER_DB_PATH),
        
        remember_max_results=max(
            1,
            env_int("PTT_REMEMBER_MAX_RESULTS", int(sqlite_cfg.get("max_results", global_max_results))),
        ),
        
        query_rewrite_enabled=env_bool(
            "PTT_QUERY_REWRITE_ENABLED", env_bool("PTT_GROQ_REWRITE_ENABLED", bool(rewrite_cfg.get("enabled", False)))
        ),
        
        llm_api_key=env_str("OPENAI_API_KEY", env_str("GROQ_API_KEY", "")).strip(),
        llm_base_url=env_str("OPENAI_BASE_URL", env_str("GROQ_BASE_URL", "")).strip(),
        
        llm_model=default_model
    )

    # 打印加载后的配置信息（隐藏敏感 Key）
    safe_config = {
        k: (v if "api_key" not in k else ("***" if v else "None"))
        for k, v in config.__dict__.items()
    }
    log(f"Storage configuration loaded: {json.dumps(safe_config, ensure_ascii=False, indent=2)}")
    
    return config


class BaseRememberStore:
    def add(self, *, memory: str, original_text: str = "") -> str:
        raise NotImplementedError

    def find(self, *, query: str) -> str:
        raise NotImplementedError


class BaseHistoryStore:
    def persist(self, entry: SessionHistoryRecord) -> None:
        raise NotImplementedError

    def list_recent(
        self, *, limit: int = 10, query: str = ""
    ) -> list[SessionHistoryRecord]:
        raise NotImplementedError

    def delete(self, *, session_id: str) -> None:
        raise NotImplementedError


def migrate_history_table(
    source_db_path: str | Path,
    target_db_path: str | Path,
) -> int:
    source = Path(source_db_path).expanduser()
    target = Path(target_db_path).expanduser()
    if not source.exists():
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    source_conn.row_factory = sqlite3.Row
    try:
        rows = source_conn.execute(
            """
            SELECT
                session_id,
                started_at,
                ended_at,
                transcript,
                reply,
                peak_level,
                mean_level,
                auto_closed,
                reopened_by_click,
                mode,
                created_at
            FROM session_histories
            ORDER BY id ASC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        source_conn.close()
        return 0
    finally:
        source_conn.close()

    target_conn = sqlite3.connect(target)
    try:
        target_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_histories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL UNIQUE,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                transcript TEXT NOT NULL,
                reply TEXT NOT NULL,
                peak_level REAL NOT NULL,
                mean_level REAL NOT NULL,
                auto_closed INTEGER NOT NULL,
                reopened_by_click INTEGER NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        target_conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_session_histories_started_at
            ON session_histories(started_at DESC)
            """
        )
        with target_conn:
            for row in rows:
                target_conn.execute(
                    """
                    INSERT INTO session_histories (
                        session_id,
                        started_at,
                        ended_at,
                        transcript,
                        reply,
                        peak_level,
                        mean_level,
                        auto_closed,
                        reopened_by_click,
                        mode,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        started_at = excluded.started_at,
                        ended_at = excluded.ended_at,
                        transcript = excluded.transcript,
                        reply = excluded.reply,
                        peak_level = excluded.peak_level,
                        mean_level = excluded.mean_level,
                        auto_closed = excluded.auto_closed,
                        reopened_by_click = excluded.reopened_by_click,
                        mode = excluded.mode,
                        created_at = excluded.created_at
                    """,
                    (
                        str(row["session_id"]),
                        str(row["started_at"]),
                        str(row["ended_at"]),
                        str(row["transcript"]),
                        str(row["reply"]),
                        float(row["peak_level"]),
                        float(row["mean_level"]),
                        int(row["auto_closed"]),
                        int(row["reopened_by_click"]),
                        str(row["mode"]),
                        str(row["created_at"]),
                    ),
                )
    finally:
        target_conn.close()
    return len(rows)


def create_mem0_client(api_key: str) -> Any:
    from mem0 import MemoryClient

    return MemoryClient(api_key=api_key)


def _localize_timestamp_fields(payload: Any) -> Any:
    if isinstance(payload, dict):
        localized: dict[str, Any] = {}
        for key, value in payload.items():
            if (
                key in {"created_at", "updated_at"}
                and isinstance(value, str)
                and value.strip()
            ):
                localized[key] = format_local_datetime(value)
            else:
                localized[key] = _localize_timestamp_fields(value)
        return localized
    if isinstance(payload, list):
        return [_localize_timestamp_fields(item) for item in payload]
    return payload


def _extract_mem0_results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        results = payload.get("results", [])
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _tokenize_for_match(query: str) -> list[str]:
    raw = str(query or "").strip()
    if not raw:
        return []
    tokens = [token.strip() for token in re.split(r"[\s,，。！？；:：/|]+", raw) if token.strip()]
    return tokens or [raw]


def _quote_match_token(token: str) -> str:
    escaped = token.replace('"', '""').strip()
    return f'"{escaped}"' if escaped else ""


def _default_match_query(query: str) -> str:
    tokens = _tokenize_for_match(query)
    if not tokens:
        return ""
    if len(tokens) == 1:
        return _quote_match_token(tokens[0])
    return " OR ".join(_quote_match_token(token) for token in tokens if token)


def _keywords_from_match_query(match_query: str, raw_query: str) -> list[str]:
    quoted = re.findall(r'"([^"]+)"', str(match_query or ""))
    cleaned = [item.strip() for item in quoted if item.strip()]
    if cleaned:
        return cleaned
    return _tokenize_for_match(raw_query)


class LLMKeywordRewriter:
    def __init__(
        self,
        *,
        api_key: str,
        llm_model: str,
        base_url: str = "",
    ) -> None:
        self.api_key = api_key.strip()
        self.model = llm_model.strip()
        self.base_url = base_url.strip()
        self._client: Any | None = None

    def _client_instance(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            client_kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._client = OpenAI(**client_kwargs)
        return self._client

    def rewrite(self, query: str) -> str:
        cleaned_query = str(query or "").strip()
        if not cleaned_query:
            return ""
            
        workflow = _require_mapping(load_workflow_config(), "workflow")
        prompts = _require_mapping(workflow.get("prompts"), "prompts")
        
        # 统一从 prompts.query_rewrite 读取
        rewrite_cfg = _require_mapping(
            prompts.get("query_rewrite"), "prompts.query_rewrite"
        )
        system_prompt = str(rewrite_cfg.get("system_prompt", "")).strip()
        if not system_prompt:
            raise RuntimeError(
                "workflow config missing required section: prompts.query_rewrite.system_prompt"
            )
            
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {"role": "user", "content": cleaned_query},
        ]
        
        log_llm_prompt("keyword rewrite", messages)
        response = self._client_instance().chat.completions.create(
            model=self.model,
            temperature=0,
            messages=messages,
        )
        content = str(response.choices[0].message.content or "").strip()
        log_multiline("keyword rewrite raw", content)
        
        # 优先提取纯文本关键词（新 Prompt 要求）
        text_result = re.sub(r"(?is)<think>.*?</think>", "", content).strip()
        
        # 兼容性：如果模型还是返回了 JSON（虽然新 Prompt 不要求）
        cleaned_keywords = []
        if "{" in text_result and "}" in text_result:
            try:
                json_start = text_result.find("{")
                json_end = text_result.rfind("}") + 1
                payload = json.loads(text_result[json_start:json_end])
                keywords = payload.get("keywords", []) if isinstance(payload, dict) else []
                cleaned_keywords = [str(k).strip() for k in keywords if str(k).strip()]
            except Exception:
                pass
        
        if not cleaned_keywords:
            # 按换行、逗号或空格分割，防止模型返回了多个词
            raw_tokens = re.split(r"[\n,，\s]+", text_result)
            cleaned_keywords = [t.strip() for t in raw_tokens if t.strip()]

        log(
            "keyword rewrite parsed: "
            + json.dumps(cleaned_keywords, ensure_ascii=False)
        )
        
        if not cleaned_keywords:
            return _quote_match_token(cleaned_query)
            
        # 构造 FTS5 查询语句 (OR 逻辑)
        rewritten_query = " OR ".join(
            _quote_match_token(keyword) for keyword in cleaned_keywords if keyword
        )
        log(f"keyword rewrite match_query: {rewritten_query}")
        return rewritten_query


class LLMMemoryTranslator:
    def __init__(
        self,
        *,
        api_key: str,
        llm_model: str,
        base_url: str = "",
    ) -> None:
        self.api_key = api_key.strip()
        self.model = llm_model.strip()
        self.base_url = base_url.strip()
        self._client: Any | None = None

    def _client_instance(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            client_kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._client = OpenAI(**client_kwargs)
        return self._client

    def translate(self, text: str) -> str:
        cleaned_text = str(text or "").strip()
        if not cleaned_text:
            return ""
        workflow = _require_mapping(load_workflow_config(), "workflow")
        prompts = _require_mapping(workflow.get("prompts"), "prompts")
        translate_cfg = _require_mapping(
            prompts.get("memory_translate"), "prompts.memory_translate"
        )
        system_prompt = str(translate_cfg.get("system_prompt", "")).strip()
        if not system_prompt:
            raise RuntimeError(
                "workflow config missing required section: prompts.memory_translate.system_prompt"
            )
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {"role": "user", "content": cleaned_text},
        ]
        log_llm_prompt("memory translate", messages)
        response = self._client_instance().chat.completions.create(
            model=self.model,
            temperature=0,
            messages=messages,
        )
        content = str(response.choices[0].message.content or "").strip()
        log_multiline("memory translate raw", content)
        translated_text = re.sub(
            r"(?is)<think>.*?</think>",
            "",
            content,
        ).strip() or cleaned_text
        log(f"memory translate parsed: {translated_text}")
        return translated_text


class Mem0RememberStore(BaseRememberStore):
    def __init__(
        self,
        *,
        api_key: str = "",
        user_id: str = "soj",
        app_id: str = "voice-assistant",
        client: Any | None = None,
    ) -> None:
        if client is None and not api_key.strip():
            raise RuntimeError("mem0 配置缺失：MEM0_API_KEY")
        self.client = client if client is not None else create_mem0_client(api_key)
        self.user_id = user_id.strip() or "soj"
        self.app_id = app_id.strip()

    def _write_scope_kwargs(self) -> dict[str, Any]:
        kwargs = {"user_id": self.user_id, "async_mode": False}
        if self.app_id:
            kwargs["app_id"] = self.app_id
        return kwargs

    def _read_scope_kwargs(self) -> dict[str, Any]:
        return {
            "filters": {
                "OR": [
                    {"AND": [{"user_id": self.user_id}]},
                    {
                        "AND": [
                            {"user_id": self.user_id},
                            {"OR": [{"app_id": "*"}, {"agent_id": "*"}]},
                        ]
                    },
                ]
            }
        }

    def add(self, *, memory: str, original_text: str = "") -> str:
        messages = [{"role": "user", "content": memory}]
        kwargs = self._write_scope_kwargs()
        if original_text.strip():
            kwargs["metadata"] = {"original_text": original_text.strip()}
        try:
            response = self.client.add(messages, **kwargs)
        except TypeError:
            kwargs.pop("metadata", None)
            response = self.client.add(messages, **kwargs)
        stored_memory = memory
        if isinstance(response, list) and response:
            first = response[0]
            if isinstance(first, dict):
                stored_memory = str(
                    first.get("memory") or first.get("data", {}).get("memory") or memory
                )
        return f"✅ 已记录：{stored_memory}"

    def find(self, *, query: str) -> str:
        response = self.client.search(query, **self._read_scope_kwargs())
        return json.dumps(_localize_timestamp_fields(response), ensure_ascii=False)

    def get_all(self) -> list[dict[str, Any]]:
        response = self.client.get_all(**self._read_scope_kwargs())
        return _extract_mem0_results(response)


class SQLiteFTS5RememberStore(BaseRememberStore):
    def __init__(
        self,
        *,
        db_path: str | Path,
        max_results: int = 3,
        keyword_rewriter: KeywordRewriter | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.max_results = max(1, int(max_results))
        self.keyword_rewriter = keyword_rewriter
        self.table_name = "remember_entries"
        self.fts_table_name = "remember_entries_simple_fts"
        self.use_simple_query = False

    def _load_simple_extension(self, conn: sqlite3.Connection) -> bool:
        extension_path = SIMPLE_EXTENSION_PATH.expanduser()
        if not extension_path.is_file():
            return False
        conn.enable_load_extension(True)
        try:
            conn.load_extension(str(extension_path))
        finally:
            conn.enable_load_extension(False)
        return True

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        self.use_simple_query = self._load_simple_extension(conn)
        fts_tokenizer_clause = ",\n                tokenize='simple'" if self.use_simple_query else ""
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                id TEXT PRIMARY KEY,
                source_memory_id TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL,
                original_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({self.table_name})").fetchall()
        }
        if "source_memory_id" not in columns:
            conn.execute(
                f"""
                ALTER TABLE {self.table_name}
                ADD COLUMN source_memory_id TEXT NOT NULL DEFAULT ''
                """
            )
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {self.fts_table_name}
            USING fts5(
                memory,
                original_text,
                item_id UNINDEXED
                -- NOTE: tokenize='simple' requires libsimple.dylib; fall back to default tokenizer if absent.
                {fts_tokenizer_clause}
            )
            """
        )
        conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{self.table_name}_updated_at
            ON {self.table_name}(updated_at DESC)
            """
        )
        conn.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{self.table_name}_source_memory_id
            ON {self.table_name}(source_memory_id)
            WHERE source_memory_id != ''
            """
        )
        conn.execute(f"DELETE FROM {self.fts_table_name}")
        conn.execute(
            f"""
            INSERT INTO {self.fts_table_name} (
                memory,
                original_text,
                item_id
            )
            SELECT
                memory,
                original_text,
                id
            FROM {self.table_name}
            """
        )
        return conn

    def add(
        self,
        *,
        memory: str,
        original_text: str = "",
        source_memory_id: str = "",
    ) -> str:
        item_id = uuid.uuid4().hex
        timestamp = _now_iso()
        stored_memory = str(memory or "").strip()
        stored_original_text = str(original_text or "").strip()
        stored_source_memory_id = str(source_memory_id or "").strip()
        with contextlib.closing(self._connect()) as conn:
            with conn:
                if stored_source_memory_id:
                    conn.execute(
                        f"DELETE FROM {self.fts_table_name} WHERE item_id IN (SELECT id FROM {self.table_name} WHERE source_memory_id = ?)",
                        (stored_source_memory_id,),
                    )
                    conn.execute(
                        f"DELETE FROM {self.table_name} WHERE source_memory_id = ?",
                        (stored_source_memory_id,),
                    )
                conn.execute(
                    f"""
                    INSERT INTO {self.table_name} (
                        id,
                        source_memory_id,
                        memory,
                        original_text,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        stored_source_memory_id,
                        stored_memory,
                        stored_original_text,
                        timestamp,
                        timestamp,
                    ),
                )
                conn.execute(
                    f"""
                    INSERT INTO {self.fts_table_name} (
                        memory,
                        original_text,
                        item_id
                    ) VALUES (?, ?, ?)
                    """,
                    (stored_memory, stored_original_text, item_id),
                )
        return f"✅ 已记录：{stored_memory}"

    def upsert(
        self,
        *,
        source_memory_id: str,
        memory: str,
        original_text: str = "",
    ) -> str:
        return self.add(
            source_memory_id=source_memory_id,
            memory=memory,
            original_text=original_text,
        )

    def has_any_rows(self) -> bool:
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                f"SELECT 1 FROM {self.table_name} LIMIT 1"
            ).fetchone()
        return row is not None

    def _match_query(self, query: str) -> str:
        cleaned_query = str(query or "").strip()
        if not cleaned_query:
            return ""

        # 1. 尝试获取重写后的关键词（如果有 rewriter）
        rewritten_query = ""
        keywords: list[str] = []
        if self.keyword_rewriter:
            try:
                # 这里的 rewrite 应该已经返回了 "word1 OR word2" 格式
                rewritten_query = str(self.keyword_rewriter.rewrite(cleaned_query)).strip()
                keywords = _keywords_from_match_query(rewritten_query, cleaned_query)
            except Exception as e:
                log(f"Keyword rewrite failed: {e}")

        # 2. 如果重写失败或没有 rewriter，使用默认分词
        if not rewritten_query:
            rewritten_query = _default_match_query(cleaned_query)
            keywords = _tokenize_for_match(cleaned_query)

        # 3. 根据是否使用 libsimple 构造最终的 MATCH 语法
        if self.use_simple_query:
            # libsimple 的 simple_query() 函数接受以空格分隔的关键词，默认行为通常符合预期
            # 但为了保证 OR 逻辑，我们重新组合它们
            match_query = " ".join(keywords).strip() or cleaned_query
            log_info = {
                "query": cleaned_query,
                "match_query": match_query,
                "keywords": keywords,
                "rewrite": bool(self.keyword_rewriter),
                "fts": "simple_query",
            }
        else:
            match_query = rewritten_query
            log_info = {
                "query": cleaned_query,
                "match_query": match_query,
                "keywords": keywords,
                "rewrite": bool(self.keyword_rewriter),
                "fts": "standard",
            }

        log("remember search input: " + json.dumps(log_info, ensure_ascii=False))
        return match_query

    def find(self, *, query: str) -> str:
        match_query = self._match_query(query)
        if not match_query:
            return json.dumps({"results": []}, ensure_ascii=False)
        with contextlib.closing(self._connect()) as conn:
            match_sql = (
                "simple_query(?)" if self.use_simple_query else "?"
            )
            log(
                "remember search sql: fts5 match="
                + (f"simple_query({match_query})" if self.use_simple_query else match_query)
            )
            rows = conn.execute(
                f"""
                SELECT
                    items.id,
                    items.memory,
                    items.original_text,
                    items.created_at,
                    items.updated_at
                FROM {self.fts_table_name} fts
                JOIN {self.table_name} items ON items.id = fts.item_id
                WHERE {self.fts_table_name} MATCH {match_sql}
                ORDER BY bm25({self.fts_table_name}), items.updated_at DESC
                LIMIT ?
                """,
                (match_query, self.max_results),
            ).fetchall()
            if not rows:
                keywords = (
                    _tokenize_for_match(match_query)
                    if self.use_simple_query
                    else _keywords_from_match_query(match_query, query)
                )
                if keywords:
                    log(
                        "remember search fallback: "
                        + json.dumps(
                            {
                                "strategy": "like",
                                "keywords": keywords,
                            },
                            ensure_ascii=False,
                        )
                    )
                    like_clauses = " OR ".join(
                        "(memory LIKE ? OR original_text LIKE ?)"
                        for _ in keywords
                    )
                    params: list[Any] = []
                    for keyword in keywords:
                        pattern = f"%{keyword}%"
                        params.extend([pattern, pattern])
                        log(
                            "remember search like keyword: "
                            + json.dumps(
                                {"keyword": keyword, "pattern": pattern},
                                ensure_ascii=False,
                            )
                        )
                    params.append(self.max_results)
                    rows = conn.execute(
                        f"""
                        SELECT
                            id,
                            memory,
                            original_text,
                            created_at,
                            updated_at
                        FROM {self.table_name}
                        WHERE {like_clauses}
                        ORDER BY updated_at DESC
                        LIMIT ?
                        """,
                        params,
                    ).fetchall()
        results = [
            {
                "id": str(row["id"]),
                "memory": str(row["memory"]),
                "original_text": str(row["original_text"]),
                "created_at": format_local_datetime(str(row["created_at"])),
                "updated_at": format_local_datetime(str(row["updated_at"])),
                "score": round(0.99 - (index * 0.01), 2),
                "metadata": {"original_text": str(row["original_text"])},
            }
            for index, row in enumerate(rows)
        ]
        return json.dumps({"results": results}, ensure_ascii=False)


def migrate_mem0_memories_to_sqlite(
    *,
    source_store: Mem0RememberStore,
    target_store: SQLiteFTS5RememberStore,
    translator: MemoryTranslator,
    page_size: int = 100,
) -> int:
    copied = 0
    page = 1
    effective_page_size = max(1, int(page_size))
    while True:
        payload = source_store.client.get_all(
            **source_store._read_scope_kwargs(),
            page=page,
            page_size=effective_page_size,
        )
        items = _extract_mem0_results(payload)
        if not items:
            break
        for item in items:
            source_memory_id = str(item.get("id", "")).strip()
            source_memory = str(item.get("memory") or item.get("text") or "").strip()
            if not source_memory:
                continue
            raw_metadata = item.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            original_text = str(
                metadata.get("original_text")
                or metadata.get("source_text")
                or item.get("original_text")
                or source_memory
            ).strip()
            translated_memory = translator.translate(source_memory) or source_memory
            target_store.upsert(
                source_memory_id=source_memory_id,
                memory=translated_memory,
                original_text=original_text,
            )
            copied += 1
        if len(items) < effective_page_size:
            break
        page += 1
    return copied


class NullHistoryStore(BaseHistoryStore):
    def persist(self, entry: SessionHistoryRecord) -> None:
        return None

    def list_recent(
        self, *, limit: int = 10, query: str = ""
    ) -> list[SessionHistoryRecord]:
        return []

    def delete(self, *, session_id: str) -> None:
        return None


class SQLiteHistoryStore(BaseHistoryStore):
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_histories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL UNIQUE,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                transcript TEXT NOT NULL,
                reply TEXT NOT NULL,
                peak_level REAL NOT NULL,
                mean_level REAL NOT NULL,
                auto_closed INTEGER NOT NULL,
                reopened_by_click INTEGER NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_session_histories_started_at
            ON session_histories(started_at DESC)
            """
        )
        return conn

    def persist(self, entry: SessionHistoryRecord) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO session_histories (
                        session_id,
                        started_at,
                        ended_at,
                        transcript,
                        reply,
                        peak_level,
                        mean_level,
                        auto_closed,
                        reopened_by_click,
                        mode,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        started_at = excluded.started_at,
                        ended_at = excluded.ended_at,
                        transcript = excluded.transcript,
                        reply = excluded.reply,
                        peak_level = excluded.peak_level,
                        mean_level = excluded.mean_level,
                        auto_closed = excluded.auto_closed,
                        reopened_by_click = excluded.reopened_by_click,
                        mode = excluded.mode
                    """,
                    (
                        entry.session_id,
                        entry.started_at,
                        entry.ended_at,
                        entry.transcript,
                        entry.reply,
                        entry.peak_level,
                        entry.mean_level,
                        int(entry.auto_closed),
                        int(entry.reopened_by_click),
                        entry.mode,
                        entry.started_at,
                    ),
                )

    def list_recent(
        self, *, limit: int = 10, query: str = ""
    ) -> list[SessionHistoryRecord]:
        sql = """
            SELECT
                session_id,
                started_at,
                ended_at,
                transcript,
                reply,
                peak_level,
                mean_level,
                auto_closed,
                reopened_by_click,
                mode
            FROM session_histories
        """
        params: list[Any] = []
        trimmed_query = query.strip()
        if trimmed_query:
            sql += " WHERE transcript LIKE ? OR reply LIKE ?"
            pattern = f"%{trimmed_query}%"
            params.extend([pattern, pattern])
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(max(1, limit))
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            SessionHistoryRecord(
                session_id=str(row["session_id"]),
                started_at=str(row["started_at"]),
                ended_at=str(row["ended_at"]),
                transcript=str(row["transcript"]),
                reply=str(row["reply"]),
                peak_level=float(row["peak_level"]),
                mean_level=float(row["mean_level"]),
                auto_closed=bool(row["auto_closed"]),
                reopened_by_click=bool(row["reopened_by_click"]),
                mode=str(row["mode"]),
            )
            for row in rows
        ]

    def delete(self, *, session_id: str) -> None:
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "DELETE FROM session_histories WHERE session_id = ?",
                    (session_id.strip(),),
                )


class StorageService:
    def __init__(self, config: StorageConfig) -> None:
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
        self._history_store: BaseHistoryStore = SQLiteHistoryStore(
            self.config.history_db_path
        )

    @classmethod
    def from_env(cls) -> "StorageService":
        return cls(load_storage_config())

    def close(self) -> None:
        return None

    def _sqlite_keyword_rewriter(self) -> KeywordRewriter | None:
        if not self.config.query_rewrite_enabled:
            return None
        if not self.config.llm_api_key.strip():
            return None
        return LLMKeywordRewriter(
            api_key=self.config.llm_api_key,
            llm_model=self.config.llm_model,
            base_url=self.config.llm_base_url,
        )

    def _sqlite_memory_translator(self) -> MemoryTranslator | None:
        if not self.config.llm_api_key.strip():
            return None
        return LLMMemoryTranslator(
            api_key=self.config.llm_api_key,
            llm_model=self.config.llm_model,
            base_url=self.config.llm_base_url,
        )

    def _bootstrap_sqlite_from_mem0(
        self, store: SQLiteFTS5RememberStore
    ) -> int:
        if store.has_any_rows():
            return 0
        if not self.config.mem0_api_key.strip():
            return 0
        translator = self._sqlite_memory_translator()
        if translator is None:
            return 0
        source_store = Mem0RememberStore(
            api_key=self.config.mem0_api_key,
            user_id=self.config.mem0_user_id,
            app_id=self.config.mem0_app_id,
        )
        copied = migrate_mem0_memories_to_sqlite(
            source_store=source_store,
            target_store=store,
            translator=translator,
        )
        log(f"sqlite remember bootstrap copied={copied}")
        return copied

    def remember_store(self) -> BaseRememberStore:
        if self.config.backend == "sqlite_fts5":
            store = SQLiteFTS5RememberStore(
                db_path=self.config.remember_db_path,
                max_results=self.config.remember_max_results,
                keyword_rewriter=self._sqlite_keyword_rewriter(),
            )
            self._bootstrap_sqlite_from_mem0(store)
            return store
        return Mem0RememberStore(
            api_key=self.config.mem0_api_key,
            user_id=self.config.mem0_user_id,
            app_id=self.config.mem0_app_id,
        )

    def history_store(self) -> BaseHistoryStore:
        return self._history_store
