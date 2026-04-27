from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from .intent import IntentExecutionRunner
from ..models.history import build_storage_config
from ..storage import StorageService
from ..utils.env import (
    WORKFLOW_CONFIG_PATH,
    expand_env_placeholders,
    load_json_file,
)
from ..utils.logging import log, log_llm_prompt, log_multiline
from ..utils.shell import parse_json_output
from ..utils.text import strip_think_tags, current_time_text


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
        self.client = AsyncOpenAI(**client_kwargs)
        self.cfg = cfg
        self.model = str(cfg.llm_model)
        self.summary_model = str(getattr(cfg, "llm_summarize_model", "") or self.model)
        self._storage_service: StorageService | None = None
        self._load_workflow_config()

    def _load_workflow_config(self) -> None:
        workflow_data = load_json_file(WORKFLOW_CONFIG_PATH)
        self.workflow = expand_env_placeholders(workflow_data)

    def _storage(self) -> StorageService:
        if self._storage_service is None:
            self._storage_service = StorageService(build_storage_config(self.cfg))
        return self._storage_service

    async def _analyze_intent_async(self, transcript: str) -> dict[str, str]:
        prompts = self.workflow.get("prompts", {})
        intent_cfg = prompts.get("memory_chat_intent", {})
        system_prompt = intent_cfg.get("system_prompt", "你是语音助手意图分析器。")
        
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            },
            {"role": "user", "content": transcript},
        ]
        log_llm_prompt("memory-chat intent", messages)
        try:
            response = await self.client.chat.completions.create(
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

    def _memory_context_items(
        self, 
        transcript: str, 
        start_date: str | None = None, 
        end_date: str | None = None
    ) -> list[dict[str, Any]]:
        try:
            remember_store = self._storage().remember_store()
            raw = remember_store.find(
                query=transcript,
                start_date=start_date,
                end_date=end_date
            )
            extracted = remember_store.extract_summary_items(raw)
        except Exception:
            log("memory-chat memory search failed")
            return []

        items = extracted.get("items", []) if isinstance(extracted, dict) else []
        return [item for item in items if isinstance(item, dict)]

    def _build_messages(
        self,
        transcript: str,
        *,
        intent: dict[str, str],
        memory_context: str,
    ) -> list[dict[str, str]]:
        prompts = self.workflow.get("prompts", {})
        summary_cfg = prompts.get("memory_chat_summary", {})
        system_prompt_tpl = summary_cfg.get("system_prompt", "你是本地语音助手。")
        
        time_text = current_time_text()
        system_prompt = system_prompt_tpl.replace("${PTT_CURRENT_TIME}", time_text)
        
        return [
            {
                "role": "system",
                "content": system_prompt,
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

    async def run_async(
        self, 
        transcript: str, 
        pre_extracted_intent: dict[str, Any] | None = None
    ) -> str:
        # 1. 意图分析：如果外部没传，才自己分析
        if pre_extracted_intent:
            intent = pre_extracted_intent
        else:
            intent = await self._analyze_intent_async(transcript)

        if intent.get("intent") == "record":
            log("memory-chat routing record intent to structured record flow")
            return await IntentExecutionRunner(self.cfg).run_async(transcript)

        # 2. 如果不是记录，说明是查询类。先查数据库内容。
        # 提取日期范围
        args = intent.get("args", {})
        start_date = args.get("start_date")
        end_date = args.get("end_date")
        
        items = self._memory_context_items(
            transcript, 
            start_date=start_date, 
            end_date=end_date
        )
        
        # 3. 判断是否命中记忆
        if not items:
            log("memory-chat memory search: no related memory hits")
            # 在没有命中记忆的情况下，如果用户明确处于 memory-chat 模式（意图分析结果通常也是 chat），
            # 那么我们会进入兜底回答流程。
            memory_context = "没有命中相关记忆。"
            if start_date or end_date:
                date_info = f"{start_date or ''} 到 {end_date or ''}".strip(" 到 ")
                memory_context = f"在 {date_info} 期间没有命中相关记忆。"
        else:
            memory_context = _format_memory_context_items(items)
            log_multiline("memory-chat memory context", memory_context)

        # 4. 调用 LLM 进行总结或兜底回答
        messages = self._build_messages(
            transcript,
            intent=intent,
            memory_context=memory_context,
        )
        log_llm_prompt("memory-chat summary", messages)
        response = await self.client.chat.completions.create(
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
            
        # 组装返回的 payload
        output_payload = {
            "reply": reply,
            "query": transcript,
            "memories": items
        }
        return json.dumps(output_payload, ensure_ascii=False)

    def run(self, transcript: str) -> str:
        import asyncio
        return asyncio.run(self.run_async(transcript))
