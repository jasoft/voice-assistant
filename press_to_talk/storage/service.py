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

from press_to_talk.utils.text import format_local_datetime

APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY_DB_PATH = APP_ROOT / "data" / "voice_assistant.sqlite3"
DEFAULT_REMEMBER_DB_PATH = APP_ROOT / "data" / "voice_assistant.sqlite3"
WORKFLOW_CONFIG_PATH = APP_ROOT / "workflow_config.json"


@dataclass
class StorageConfig:
    backend: str = "mem0"
    mem0_api_key: str = ""
    mem0_user_id: str = "soj"
    mem0_app_id: str = "voice-assistant"
    mem0_min_score: float = 0.8
    mem0_max_items: int = 3
    history_db_path: str = str(DEFAULT_HISTORY_DB_PATH)
    remember_db_path: str = str(DEFAULT_REMEMBER_DB_PATH)
    remember_max_results: int = 3
    groq_rewrite_enabled: bool = False
    groq_rewrite_model: str = "qwen/qwen3-32b"
    groq_rewrite_api_key: str = ""
    groq_rewrite_base_url: str = ""


@dataclass
class RememberItemRecord:
    id: str
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


def _workflow_storage_config() -> dict[str, Any]:
    workflow = load_workflow_config()
    storage = workflow.get("storage", {}) if isinstance(workflow, dict) else {}
    return storage if isinstance(storage, dict) else {}


def load_storage_config() -> StorageConfig:
    storage_cfg = _workflow_storage_config()
    sqlite_cfg = storage_cfg.get("sqlite_fts5", {})
    sqlite_cfg = sqlite_cfg if isinstance(sqlite_cfg, dict) else {}
    rewrite_cfg = sqlite_cfg.get("groq_query_rewrite", {})
    rewrite_cfg = rewrite_cfg if isinstance(rewrite_cfg, dict) else {}

    raw_app_id = os.environ.get("MEM0_APP_ID")
    app_id = "voice-assistant" if raw_app_id is None else str(raw_app_id).strip()

    configured_backend = str(storage_cfg.get("provider", "mem0")).strip() or "mem0"
    remember_db_path = (
        str(sqlite_cfg.get("db_path", str(DEFAULT_REMEMBER_DB_PATH))).strip()
        or str(DEFAULT_REMEMBER_DB_PATH)
    )
    remember_max_results = max(1, int(sqlite_cfg.get("max_results", 3)))
    rewrite_enabled = bool(rewrite_cfg.get("enabled", False))
    rewrite_model = (
        str(
            rewrite_cfg.get(
                "model",
                env_str("PTT_GROQ_MODEL", env_str("PTT_MODEL", "qwen/qwen3-32b")),
            )
        ).strip()
        or "qwen/qwen3-32b"
    )

    return StorageConfig(
        backend=str(env_str("PTT_REMEMBER_BACKEND", configured_backend)).strip()
        or configured_backend,
        mem0_api_key=env_str("MEM0_API_KEY", "").strip(),
        mem0_user_id=str(env_str("MEM0_USER_ID", "soj")).strip() or "soj",
        mem0_app_id=app_id,
        mem0_min_score=env_float("MEM0_MIN_SCORE", 0.8),
        mem0_max_items=max(1, env_int("MEM0_MAX_ITEMS", 3)),
        history_db_path=env_str(
            "PTT_HISTORY_DB_PATH", str(DEFAULT_HISTORY_DB_PATH)
        ).strip()
        or str(DEFAULT_HISTORY_DB_PATH),
        remember_db_path=env_str("PTT_REMEMBER_DB_PATH", remember_db_path).strip()
        or remember_db_path,
        remember_max_results=max(
            1,
            env_int("PTT_REMEMBER_MAX_RESULTS", remember_max_results),
        ),
        groq_rewrite_enabled=env_bool(
            "PTT_GROQ_REWRITE_ENABLED", rewrite_enabled
        ),
        groq_rewrite_model=str(
            env_str("PTT_GROQ_REWRITE_MODEL", rewrite_model)
        ).strip()
        or rewrite_model,
        groq_rewrite_api_key=env_str(
            "OPENAI_API_KEY", env_str("GROQ_API_KEY", "")
        ).strip(),
        groq_rewrite_base_url=env_str(
            "OPENAI_BASE_URL", env_str("GROQ_BASE_URL", "")
        ).strip(),
    )


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


class GroqKeywordRewriter:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "",
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip()
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
        response = self._client_instance().chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个 SQLite FTS5 查询改写器。"
                        "把用户原始问题拆成 2 到 5 个最可能命中的短关键词或短语。"
                        "只返回 JSON：{\"keywords\":[\"词1\",\"词2\"]}。"
                        "不要解释，不要补充其它字段。"
                    ),
                },
                {"role": "user", "content": cleaned_query},
            ],
        )
        content = str(response.choices[0].message.content or "").strip()
        payload = json.loads(content)
        keywords = payload.get("keywords", []) if isinstance(payload, dict) else []
        cleaned_keywords = [
            str(item).strip()
            for item in keywords
            if str(item).strip()
        ]
        if not cleaned_keywords:
            return _default_match_query(cleaned_query)
        return " OR ".join(
            _quote_match_token(keyword) for keyword in cleaned_keywords if keyword
        )


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

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS remember_items (
                id TEXT PRIMARY KEY,
                memory TEXT NOT NULL,
                original_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS remember_items_fts
            USING fts5(
                memory,
                original_text,
                item_id UNINDEXED,
                tokenize='unicode61'
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_remember_items_updated_at
            ON remember_items(updated_at DESC)
            """
        )
        return conn

    def add(self, *, memory: str, original_text: str = "") -> str:
        item_id = uuid.uuid4().hex
        timestamp = _now_iso()
        stored_memory = str(memory or "").strip()
        stored_original_text = str(original_text or "").strip()
        with contextlib.closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO remember_items (
                        id,
                        memory,
                        original_text,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        stored_memory,
                        stored_original_text,
                        timestamp,
                        timestamp,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO remember_items_fts (
                        memory,
                        original_text,
                        item_id
                    ) VALUES (?, ?, ?)
                    """,
                    (stored_memory, stored_original_text, item_id),
                )
        return f"✅ 已记录：{stored_memory}"

    def _match_query(self, query: str) -> str:
        cleaned_query = str(query or "").strip()
        if not cleaned_query:
            return ""
        if self.keyword_rewriter is None:
            return _default_match_query(cleaned_query)
        try:
            rewritten = str(self.keyword_rewriter.rewrite(cleaned_query)).strip()
            return rewritten or _default_match_query(cleaned_query)
        except Exception:
            return _default_match_query(cleaned_query)

    def find(self, *, query: str) -> str:
        match_query = self._match_query(query)
        if not match_query:
            return json.dumps({"results": []}, ensure_ascii=False)
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT
                    items.id,
                    items.memory,
                    items.original_text,
                    items.created_at,
                    items.updated_at
                FROM remember_items_fts fts
                JOIN remember_items items ON items.id = fts.item_id
                WHERE remember_items_fts MATCH ?
                ORDER BY bm25(remember_items_fts), items.updated_at DESC
                LIMIT ?
                """,
                (match_query, self.max_results),
            ).fetchall()
            if not rows:
                keywords = _keywords_from_match_query(match_query, query)
                if keywords:
                    like_clauses = " OR ".join(
                        "(memory LIKE ? OR original_text LIKE ?)"
                        for _ in keywords
                    )
                    params: list[Any] = []
                    for keyword in keywords:
                        pattern = f"%{keyword}%"
                        params.extend([pattern, pattern])
                    params.append(self.max_results)
                    rows = conn.execute(
                        f"""
                        SELECT
                            id,
                            memory,
                            original_text,
                            created_at,
                            updated_at
                        FROM remember_items
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
            groq_rewrite_enabled=config.groq_rewrite_enabled,
            groq_rewrite_model=config.groq_rewrite_model,
            groq_rewrite_api_key=config.groq_rewrite_api_key,
            groq_rewrite_base_url=config.groq_rewrite_base_url,
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
        if not self.config.groq_rewrite_enabled:
            return None
        if not self.config.groq_rewrite_api_key.strip():
            return None
        return GroqKeywordRewriter(
            api_key=self.config.groq_rewrite_api_key,
            model=self.config.groq_rewrite_model,
            base_url=self.config.groq_rewrite_base_url,
        )

    def remember_store(self) -> BaseRememberStore:
        if self.config.backend == "sqlite_fts5":
            return SQLiteFTS5RememberStore(
                db_path=self.config.remember_db_path,
                max_results=self.config.remember_max_results,
                keyword_rewriter=self._sqlite_keyword_rewriter(),
            )
        return Mem0RememberStore(
            api_key=self.config.mem0_api_key,
            user_id=self.config.mem0_user_id,
            app_id=self.config.mem0_app_id,
        )

    def history_store(self) -> BaseHistoryStore:
        return self._history_store
