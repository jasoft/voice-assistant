from __future__ import annotations

from typing import Any

from openai import OpenAI

from ..models.history import build_storage_config
from ..storage import StorageService
from ..utils.text import strip_think_tags


def _format_memory_context_items(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, item in enumerate(items[:5], start=1):
        memory = str(item.get("memory", "")).strip()
        if not memory:
            continue
        created_at = str(item.get("created_at") or item.get("updated_at") or "").strip()
        prefix = f"{index}. "
        if created_at:
            lines.append(f"{prefix}{memory}（记录时间：{created_at}）")
        else:
            lines.append(f"{prefix}{memory}")
    return "\n".join(lines).strip()


class MemoryChatExecutionRunner:
    def __init__(self, cfg: Any) -> None:
        client_kwargs: dict[str, Any] = {"api_key": cfg.llm_api_key}
        if str(getattr(cfg, "llm_base_url", "") or "").strip():
            client_kwargs["base_url"] = str(cfg.llm_base_url).strip()
        self.client = OpenAI(**client_kwargs)
        self.cfg = cfg
        self.model = str(cfg.llm_model)
        self._storage_service: StorageService | None = None

    def _storage(self) -> StorageService:
        if self._storage_service is None:
            self._storage_service = StorageService(build_storage_config(self.cfg))
        return self._storage_service

    def _memory_context(self, transcript: str) -> str:
        try:
            remember_store = self._storage().remember_store()
            raw = remember_store.find(query=transcript)
            extracted = remember_store.extract_summary_items(raw)
        except Exception:
            return ""

        items = extracted.get("items", []) if isinstance(extracted, dict) else []
        normalized_items = [item for item in items if isinstance(item, dict)]
        if not normalized_items:
            return ""
        return _format_memory_context_items(normalized_items)

    def _build_messages(self, transcript: str) -> list[dict[str, str]]:
        memory_context = self._memory_context(transcript) or "没有命中相关记忆。"
        return [
            {
                "role": "system",
                "content": (
                    "你是本地语音助手的快速回答链路。"
                    "先参考我提供的相关记忆直接回答。"
                    "如果相关记忆不足，再基于常识补足，但不要假装查过网页。"
                    "回答保持简短、直接、适合语音播报。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"相关记忆：\n{memory_context}\n\n"
                    "如果相关记忆不足，可以直接说明不确定或信息不足。\n\n"
                    f"用户问题：{transcript}"
                ),
            },
        ]

    def run(self, transcript: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self._build_messages(transcript),
            temperature=0.2,
        )
        raw_reply = str(response.choices[0].message.content or "").strip()
        reply = strip_think_tags(raw_reply).strip()
        if not reply:
            raise RuntimeError("memory-chat returned empty reply")
        return reply
