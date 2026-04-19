from __future__ import annotations

import json
from typing import Any

from ..models import BaseRememberStore, RememberItemRecord, StorageConfig


def create_mem0_client(api_key: str) -> Any:
    from mem0 import MemoryClient
    return MemoryClient(api_key=api_key)


def _localize_timestamp_fields(payload: Any) -> Any:
    from press_to_talk.utils.text import format_local_datetime
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


def _load_mem0_tuning_config() -> dict[str, Any]:
    defaults = {"min_score": 0.8, "max_items": 3}
    try:
        from press_to_talk.storage.service import load_workflow_config
        workflow = load_workflow_config()
        mem0_cfg: Any = {}
        if isinstance(workflow, dict):
            storage_cfg = workflow.get("storage", {})
            if isinstance(storage_cfg, dict):
                mem0_cfg = storage_cfg.get("mem0", {})
            if not isinstance(mem0_cfg, dict) or not mem0_cfg:
                mem0_cfg = workflow.get("mem0", {})
        if not isinstance(mem0_cfg, dict):
            return defaults
        return {
            "min_score": float(mem0_cfg.get("min_score", defaults["min_score"])),
            "max_items": max(
                1,
                int(mem0_cfg.get("max_items", defaults["max_items"])),
            ),
        }
    except Exception:
        return defaults


def extract_mem0_summary_payload(
    raw_payload: str | dict[str, Any] | list[Any],
) -> dict[str, Any]:
    tuning = _load_mem0_tuning_config()
    min_score = tuning["min_score"]
    max_items = tuning["max_items"]
    payload: Any = raw_payload
    if isinstance(raw_payload, str):
        text = raw_payload.strip()
        if not text:
            return {"items": [], "raw": raw_payload}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"items": [], "raw": raw_payload}

    if isinstance(payload, dict):
        raw_items = payload.get("results")
        if raw_items is None and {"id", "memory"} & payload.keys():
            raw_items = [payload]
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    items: list[dict[str, Any]] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        data_dict = data if isinstance(data, dict) else {}
        metadata = item.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        memory = str(item.get("memory") or data_dict.get("memory") or "").strip()
        if not memory:
            continue
        extracted: dict[str, Any] = {
            "id": str(item.get("id") or data_dict.get("id") or "").strip(),
            "memory": memory,
            "score": item.get("score"),
            "created_at": str(
                item.get("created_at")
                or item.get("createdAt")
                or data_dict.get("created_at")
                or ""
            ).strip(),
            "updated_at": str(
                item.get("updated_at")
                or item.get("updatedAt")
                or data_dict.get("updated_at")
                or ""
            ).strip(),
            "metadata": metadata_dict,
            "categories": item.get("categories") or data_dict.get("categories") or [],
        }
        items.append(extracted)
    scored_items: list[dict[str, Any]] = []
    for item in items:
        score = item.get("score")
        if score is None:
            continue
        try:
            numeric_score = float(score)
        except (TypeError, ValueError):
            continue
        if numeric_score <= min_score:
            continue
        item["score"] = numeric_score
        scored_items.append(item)
    scored_items.sort(key=lambda item: float(item["score"]), reverse=True)
    items = scored_items[:max_items]
    return {"items": items, "raw": payload}


class Mem0RememberStore(BaseRememberStore):
    def __init__(
        self,
        *,
        api_key: str = "",
        user_id: str = "soj",
        app_id: str = "",
        client: Any | None = None,
    ) -> None:
        if client is None and not api_key.strip():
            raise RuntimeError("mem0 配置缺失：MEM0_API_KEY")
        self.client = client if client is not None else create_mem0_client(api_key)
        self.user_id = user_id.strip() or "soj"
        self.app_id = app_id.strip()

    @classmethod
    def from_config(cls, config: StorageConfig, **kwargs) -> Mem0RememberStore:
        return cls(
            api_key=config.mem0_api_key,
            user_id=config.mem0_user_id,
            app_id=kwargs.get("app_id", config.mem0_app_id),
        )

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

    def find(self, *, query: str, min_score: float = 0.0) -> str:
        response = self.client.search(query, **self._read_scope_kwargs())
        if min_score > 0 and isinstance(response, list):
            response = [
                item
                for item in response
                if isinstance(item, dict) and float(item.get("score", 0)) >= min_score
            ]
        return json.dumps(_localize_timestamp_fields(response), ensure_ascii=False)

    def extract_summary_items(
        self, raw_payload: str | dict[str, object] | list[object]
    ) -> dict[str, object]:
        return extract_mem0_summary_payload(raw_payload)

    def delete(self, *, memory_id: str) -> None:
        self.client.delete(memory_id)

    def list_all(self, *, limit: int = 100) -> list[RememberItemRecord]:
        response = self.client.get_all(**self._read_scope_kwargs())
        items = _extract_mem0_results(response)
        records = []
        for item in items[:limit]:
            metadata = item.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            records.append(
                RememberItemRecord(
                    id=str(item.get("id", "")),
                    source_memory_id=str(item.get("id", "")),
                    memory=str(item.get("memory") or item.get("text") or ""),
                    original_text=str(metadata.get("original_text") or ""),
                    created_at=str(item.get("created_at") or ""),
                )
            )
        return records

    def get_all(self) -> list[dict[str, Any]]:
        response = self.client.get_all(**self._read_scope_kwargs())
        return _extract_mem0_results(response)
