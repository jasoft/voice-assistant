from __future__ import annotations

from datetime import datetime
from press_to_talk.storage import (
    SessionHistoryRecord,
    StorageConfig,
    StorageService,
    load_storage_config,
)
from .config import Config, SessionHistory
from ..utils.env import APP_ROOT, env_str

def format_history_timestamp(ts: datetime | None = None) -> str:
    current = ts or datetime.now().astimezone()
    return current.isoformat(timespec="seconds")

def build_storage_config(cfg: Config) -> StorageConfig:
    config = load_storage_config()
    config.user_id = cfg.user_id
    config.mem0_api_key = env_str("MEM0_API_KEY", config.mem0_api_key).strip()
    config.mem0_user_id = (
        env_str("MEM0_USER_ID", config.mem0_user_id).strip() or config.mem0_user_id
    )
    config.history_db_path = env_str(
        "PTT_HISTORY_DB_PATH",
        str(APP_ROOT / "data" / "voice_assistant_store.sqlite3"),
    ).strip() or str(APP_ROOT / "data" / "voice_assistant_store.sqlite3")
    config.llm_api_key = cfg.llm_api_key.strip()
    config.llm_base_url = cfg.llm_base_url.strip()
    if cfg.llm_model.strip():
        config.llm_model = cfg.llm_model.strip()
    return config

class HistoryWriter:
    def __init__(self, service: StorageService) -> None:
        self.service = service

    @property
    def enabled(self) -> bool:
        return True

    @classmethod
    def from_config(cls, cfg: Config) -> "HistoryWriter":
        return cls(StorageService(build_storage_config(cfg)))

    def persist(self, entry: SessionHistory) -> None:
        self.service.history_store().persist(
            SessionHistoryRecord(
                session_id=entry.session_id,
                started_at=entry.started_at,
                ended_at=entry.ended_at,
                transcript=entry.transcript,
                reply=entry.reply,
                peak_level=entry.peak_level,
                mean_level=entry.mean_level,
                auto_closed=entry.auto_closed,
                reopened_by_click=entry.reopened_by_click,
                mode=entry.mode,
            )
        )
