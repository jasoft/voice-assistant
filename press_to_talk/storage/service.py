from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from press_to_talk.utils.text import format_local_datetime

APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY_DB_PATH = APP_ROOT / "data" / "voice_assistant.sqlite3"
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


def load_workflow_config() -> dict[str, Any]:
    try:
        with WORKFLOW_CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def load_storage_config() -> StorageConfig:
    raw_app_id = os.environ.get("MEM0_APP_ID")
    app_id = "voice-assistant" if raw_app_id is None else str(raw_app_id).strip()
    return StorageConfig(
        backend="mem0",
        mem0_api_key=env_str("MEM0_API_KEY", "").strip(),
        mem0_user_id=str(env_str("MEM0_USER_ID", "soj")).strip() or "soj",
        mem0_app_id=app_id,
        mem0_min_score=env_float("MEM0_MIN_SCORE", 0.8),
        mem0_max_items=max(
            1,
            env_int("MEM0_MAX_ITEMS", 3),
        ),
        history_db_path=env_str(
            "PTT_HISTORY_DB_PATH", str(DEFAULT_HISTORY_DB_PATH)
        ).strip()
        or str(DEFAULT_HISTORY_DB_PATH),
    )


class BaseRememberStore:
    def add(self, *, memory: str, original_text: str = "") -> str:
        raise NotImplementedError

    def find(self, *, query: str) -> str:
        raise NotImplementedError

    def list_recent(self, *, limit: int = 20) -> str:
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
        return {"filters": {"AND": [{"user_id": self.user_id}]}}

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

    def list_recent(self, *, limit: int = 20) -> str:
        response = self.client.get_all(limit=limit, **self._read_scope_kwargs())
        return json.dumps(_localize_timestamp_fields(response), ensure_ascii=False)


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
            backend="mem0",
            mem0_api_key=config.mem0_api_key,
            mem0_user_id=config.mem0_user_id,
            history_db_path=config.history_db_path,
        )
        self._history_store: BaseHistoryStore = SQLiteHistoryStore(
            self.config.history_db_path
        )

    @classmethod
    def from_env(cls) -> "StorageService":
        return cls(load_storage_config())

    def close(self) -> None:
        return None

    def remember_store(self) -> BaseRememberStore:
        return Mem0RememberStore(
            api_key=self.config.mem0_api_key,
            user_id=self.config.mem0_user_id,
            app_id=self.config.mem0_app_id,
        )

    def history_store(self) -> BaseHistoryStore:
        return self._history_store
