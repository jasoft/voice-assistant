from __future__ import annotations

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
