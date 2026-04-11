from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass
class StorageConfig:
    backend: str = "mem0"
    mem0_api_key: str = ""
    mem0_user_id: str = "soj"


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


def load_storage_config() -> StorageConfig:
    return StorageConfig(
        backend="mem0",
        mem0_api_key=env_str("MEM0_API_KEY", "").strip(),
        mem0_user_id=env_str("MEM0_USER_ID", "soj").strip() or "soj",
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

    def list_recent(self, *, limit: int = 10, query: str = "") -> list[SessionHistoryRecord]:
        raise NotImplementedError

    def delete(self, *, session_id: str) -> None:
        raise NotImplementedError


def create_mem0_client(api_key: str) -> Any:
    from mem0 import MemoryClient

    return MemoryClient(api_key=api_key)


class Mem0RememberStore(BaseRememberStore):
    def __init__(self, *, api_key: str = "", user_id: str = "soj", client: Any | None = None) -> None:
        if client is None and not api_key.strip():
            raise RuntimeError("mem0 配置缺失：MEM0_API_KEY")
        self.client = client if client is not None else create_mem0_client(api_key)
        self.user_id = user_id.strip() or "soj"

    def add(self, *, memory: str, original_text: str = "") -> str:
        messages = [{"role": "user", "content": memory}]
        kwargs: dict[str, Any] = {"user_id": self.user_id}
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
                stored_memory = str(first.get("memory") or first.get("data", {}).get("memory") or memory)
        return f"✅ 已记录：{stored_memory}"

    def find(self, *, query: str) -> str:
        response = self.client.search(query, filters={"user_id": self.user_id})
        return json.dumps(response, ensure_ascii=False)

    def list_recent(self, *, limit: int = 20) -> str:
        response = self.client.get_all(filters={"user_id": self.user_id}, limit=limit)
        return json.dumps(response, ensure_ascii=False)


class NullHistoryStore(BaseHistoryStore):
    def persist(self, entry: SessionHistoryRecord) -> None:
        return None

    def list_recent(self, *, limit: int = 10, query: str = "") -> list[SessionHistoryRecord]:
        return []

    def delete(self, *, session_id: str) -> None:
        return None


class StorageService:
    def __init__(self, config: StorageConfig) -> None:
        self.config = StorageConfig(
            backend="mem0",
            mem0_api_key=config.mem0_api_key,
            mem0_user_id=config.mem0_user_id,
        )
        self._history_store = NullHistoryStore()

    @classmethod
    def from_env(cls) -> "StorageService":
        return cls(load_storage_config())

    def close(self) -> None:
        return None

    def remember_store(self) -> BaseRememberStore:
        return Mem0RememberStore(
            api_key=self.config.mem0_api_key,
            user_id=self.config.mem0_user_id,
        )

    def history_store(self) -> BaseHistoryStore:
        return self._history_store
