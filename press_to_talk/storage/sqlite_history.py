from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

from .models import BaseHistoryStore, SessionHistory, SessionHistoryRecord


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
                user_id TEXT NOT NULL DEFAULT 'soj',
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
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_session_histories_user_id
            ON session_histories(user_id)
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
                        user_id,
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
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        user_id = excluded.user_id,
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
                        "soj", # Default user_id for SQLiteHistoryStore
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


class PeeweeHistoryStore(BaseHistoryStore):
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    def persist(self, entry: SessionHistoryRecord) -> None:
        SessionHistory.insert(
            session_id=entry.session_id,
            user_id=self.user_id,
            started_at=entry.started_at,
            ended_at=entry.ended_at,
            transcript=entry.transcript,
            reply=entry.reply,
            peak_level=entry.peak_level,
            mean_level=entry.mean_level,
            auto_closed=entry.auto_closed,
            reopened_by_click=entry.reopened_by_click,
            mode=entry.mode,
            created_at=entry.started_at,
        ).on_conflict_replace().execute()

    def list_recent(
        self, *, limit: int = 10, query: str = ""
    ) -> list[SessionHistoryRecord]:
        limit = max(1, limit)
        q = SessionHistory.select().where(SessionHistory.user_id == self.user_id)
        trimmed_query = query.strip()
        if trimmed_query:
            q = q.where(
                (SessionHistory.transcript.contains(trimmed_query))
                | (SessionHistory.reply.contains(trimmed_query))
            )
        q = q.order_by(SessionHistory.started_at.desc()).limit(limit)
        return [
            SessionHistoryRecord(
                session_id=row.session_id,
                started_at=row.started_at,
                ended_at=row.ended_at,
                transcript=row.transcript,
                reply=row.reply,
                peak_level=row.peak_level,
                mean_level=row.mean_level,
                auto_closed=row.auto_closed,
                reopened_by_click=row.reopened_by_click,
                mode=row.mode,
            )
            for row in q
        ]

    def delete(self, *, session_id: str) -> None:
        SessionHistory.delete().where(
            (SessionHistory.session_id == session_id.strip())
            & (SessionHistory.user_id == self.user_id)
        ).execute()


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
        # Check if user_id exists in source
        source_cursor = source_conn.execute("PRAGMA table_info(session_histories)")
        columns = [row[1] for row in source_cursor.fetchall()]
        has_user_id = "user_id" in columns

        select_sql = """
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
        """
        if has_user_id:
            select_sql = select_sql.replace("session_id,", "session_id, user_id,")

        select_sql += " FROM session_histories ORDER BY id ASC"
        rows = source_conn.execute(select_sql).fetchall()
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
                user_id TEXT NOT NULL DEFAULT 'soj',
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
        target_conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_session_histories_user_id
            ON session_histories(user_id)
            """
        )
        with target_conn:
            for row in rows:
                user_id = str(row["user_id"]) if has_user_id else "soj"
                target_conn.execute(
                    """
                    INSERT INTO session_histories (
                        session_id,
                        user_id,
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
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        user_id = excluded.user_id,
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
                        user_id,
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
