from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from .intent import IntentExecutionRunner
from ..models.history import build_storage_config
from ..storage import StorageService
from ..utils.logging import log, log_llm_prompt, log_multiline
from ..utils.shell import parse_json_output
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
        self.summary_model = str(getattr(cfg, "llm_summarize_model", "") or self.model)
        self._storage_service: StorageService | None = None

    def _storage(self) -> StorageService:
        if self._storage_service is None:
            self._storage_service = StorageService(build_storage_config(self.cfg))
        return self._storage_service

    def _analyze_intent(self, transcript: str) -> dict[str, str]:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是语音助手 chat-mode 的意图分析器。"
                    "只做最小判断，把当前问题判断成 record 或 chat。"
                    "record 表示用户明确要记录、保存、更新某条记忆；"
                    "其它情况一律输出 chat。"
                    '只返回 JSON，例如 {"intent":"chat","notes":"开放问答"}。'
                ),
            },
            {"role": "user", "content": transcript},
        ]
        log_llm_prompt("memory-chat intent", messages)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
            )
            raw_output = str(response.choices[0].message.content or "").strip()
            clean_output = strip_think_tags(raw_output).strip()
            log_multiline("memory-chat intent raw", raw_output)
            log_multiline("memory-chat intent cleaned", clean_output)
            try:
                payload = parse_json_output(clean_output)
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            intent = str(payload.get("intent", "")).strip().lower()
            if intent not in {"record", "chat"}:
                intent = "chat"
            notes = str(payload.get("notes", "")).strip()
            result = {"intent": intent, "notes": notes}
            log(
                "memory-chat intent parsed: "
                + json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            )
            return result
        except Exception as exc:
            log(f"memory-chat intent analysis failed: {exc}")
            return {"intent": "chat", "notes": ""}

    def _memory_context(self, transcript: str) -> str:
        try:
            remember_store = self._storage().remember_store()
            raw = remember_store.find(query=transcript)
            extracted = remember_store.extract_summary_items(raw)
        except Exception:
            log("memory-chat memory search failed; continuing without memory context")
            return ""

        items = extracted.get("items", []) if isinstance(extracted, dict) else []
        normalized_items = [item for item in items if isinstance(item, dict)]
        if not normalized_items:
            log("memory-chat memory search: no related memory hits")
            return ""
        memory_context = _format_memory_context_items(normalized_items)
        log_multiline("memory-chat memory context", memory_context)
        return memory_context

    def _build_messages(
        self,
        transcript: str,
        *,
        intent: dict[str, str],
        memory_context: str,
    ) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "你是本地语音助手 chat-mode 的最终回答链路。"
                    "先参考我提供的相关记忆回答。"
                    "如果相关记忆不足，继续根据你的知识和联网检索能力回答问题。"
                    "不要因为没命中记忆就直接回复信息不足。"
                    "只有在你确实无法确认时，再明确说明不确定。"
                    "回答保持简短、直接、适合语音播报。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"意图分析：{intent['intent']}\n"
                    f"意图说明：{intent['notes'] or '（无）'}\n\n"
                    f"相关记忆：\n{memory_context}\n\n"
                    f"用户问题：{transcript}"
                ),
            },
        ]

    def run(self, transcript: str) -> str:
        intent = self._analyze_intent(transcript)
        if intent.get("intent") == "record":
            log("memory-chat routing record intent to structured record flow")
            return IntentExecutionRunner(self.cfg).run(transcript)

        memory_context = self._memory_context(transcript) or "没有命中相关记忆。"
        messages = self._build_messages(
            transcript,
            intent=intent,
            memory_context=memory_context,
        )
        log_llm_prompt("memory-chat summary", messages)
        response = self.client.chat.completions.create(
            model=self.summary_model,
            messages=messages,
            temperature=0.2,
        )
        raw_reply = str(response.choices[0].message.content or "").strip()
        log_multiline("memory-chat summary raw", raw_reply)
        reply = strip_think_tags(raw_reply).strip()
        log_multiline("memory-chat summary cleaned", reply)
        if not reply:
            raise RuntimeError("memory-chat returned empty reply")
        return reply
