from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from peewee import Model, CharField, DateTimeField, FloatField, BooleanField, TextField, SqliteDatabase, SQL

db = SqliteDatabase(None)  # To be initialized in service


class BaseModel(Model):
    class Meta:
        database = db


class APIToken(BaseModel):
    token = CharField(primary_key=True)
    user_id = CharField(index=True)
    description = TextField(null=True)
    created_at = DateTimeField(constraints=[SQL('DEFAULT CURRENT_TIMESTAMP')])


class SessionHistory(BaseModel):
    session_id = CharField(unique=True)
    user_id = CharField(index=True)
    started_at = CharField()  # Match existing schema types
    ended_at = CharField()
    transcript = TextField()
    reply = TextField()
    peak_level = FloatField()
    mean_level = FloatField()
    auto_closed = BooleanField()
    reopened_by_click = BooleanField()
    mode = CharField()
    created_at = DateTimeField(constraints=[SQL('DEFAULT CURRENT_TIMESTAMP')])


class RememberEntry(BaseModel):
    id = CharField(primary_key=True)
    user_id = CharField(index=True)
    source_memory_id = CharField(null=True)
    memory = TextField()
    original_text = TextField()
    created_at = CharField()
    updated_at = CharField()


@dataclass
class StorageConfig:
    backend: str = "mem0"
    user_id: str = "soj"
    mem0_api_key: str = ""
    mem0_user_id: str = "soj"
    mem0_app_id: str = "voice-assistant"
    mem0_min_score: float = 0.8
    mem0_max_items: int = 20
    history_db_path: str = ""
    remember_db_path: str = ""
    remember_max_results: int = 20
    query_rewrite_enabled: bool = False
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "qwen/qwen3-32b"
    groq_rewrite_enabled: bool = False
    groq_rewrite_model: str = ""
    embedding_search_enabled: bool = False
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_max_results: int = 5
    embedding_min_score: float = 0.45
    embedding_context_min_score: float = 0.55


@dataclass
class RememberItemRecord:
    id: str
    source_memory_id: str
    memory: str
    original_text: str
    created_at: str
    updated_at: str = ""


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


class EmbeddingClient(Protocol):
    def embed_many(self, texts: list[str]) -> list[list[float]]: ...


class BaseRememberStore:
    @classmethod
    def from_config(cls, config: StorageConfig, **kwargs) -> BaseRememberStore:
        raise NotImplementedError

    def add(self, *, memory: str, original_text: str = "") -> str:
        raise NotImplementedError

    def find(
        self,
        *,
        query: str,
        min_score: float = 0.0,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> str:
        raise NotImplementedError

    def extract_summary_items(
        self, raw_payload: str | dict[str, object] | list[object]
    ) -> dict[str, object]:
        raise NotImplementedError

    def delete(self, *, memory_id: str) -> None:
        raise NotImplementedError

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[RememberItemRecord]:
        raise NotImplementedError

    def update(
        self,
        *,
        memory_id: str,
        memory: str,
        original_text: str = "",
    ) -> RememberItemRecord:
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
