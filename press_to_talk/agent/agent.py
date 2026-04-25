from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

from press_to_talk.storage import StorageService

from ..models.config import Config
from ..models.history import build_storage_config
from ..utils.env import (
    WORKFLOW_CONFIG_PATH,
    expand_env_placeholders,
    load_json_file,
)
from ..utils.logging import log, log_llm_prompt, log_multiline
from ..utils.shell import parse_json_output
from ..utils.text import (
    current_time_text,
    format_local_datetime,
    strip_think_tags,
)
from .intent import (
    salvage_truncated_intent_payload,
)


def _runtime_current_time_text() -> str:
    return current_time_text()


def _format_structured_mem0_summary(items: list[dict[str, Any]]) -> str:
    if not items:
        return "<none>"

    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        lines.append(f"第{index}条:")
        memory = str(item.get("memory", "")).strip()
        if memory:
            lines.append(f"记忆: {memory}")
        score = item.get("score")
        if score not in (None, ""):
            lines.append(f"分数: {score}")
        created_at = str(item.get("created_at") or item.get("createdAt") or "").strip()
        if created_at:
            lines.append(f"记录时间: {format_local_datetime(created_at)}")
        updated_at = str(item.get("updated_at") or item.get("updatedAt") or "").strip()
        if updated_at:
            lines.append(f"更新时间: {format_local_datetime(updated_at)}")
        metadata = item.get("metadata")
        if isinstance(metadata, dict) and metadata:
            metadata_text = ", ".join(
                f"{key}={value}"
                for key, value in metadata.items()
                if value not in (None, "")
            )
            if metadata_text:
                lines.append(f"元数据: {metadata_text}")
        categories = item.get("categories")
        if isinstance(categories, list) and categories:
            lines.append(f"分类: {', '.join(str(category) for category in categories)}")
    return "\n".join(lines)


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"workflow config missing required section: {path}")
    return value


def _render_prompt_template(template: str, values: dict[str, str]) -> str:
    rendered = str(template or "")
    for key, value in values.items():
        rendered = rendered.replace(f"${{{key}}}", value)
    return rendered


def _memory_date_prefix(timestamp: str) -> str:
    raw = str(timestamp or "").strip()
    if not raw:
        return ""
    iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if iso_match:
        return iso_match.group(0)
    cn_match = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})号", raw)
    if cn_match:
        year, month, day = cn_match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    localized = format_local_datetime(raw)
    localized_match = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})号", localized)
    if localized_match:
        year, month, day = localized_match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return ""


class OpenAICompatibleAgent:
    def __init__(self, cfg: Config) -> None:
        from openai import AsyncOpenAI

        client_kwargs: dict[str, Any] = {"api_key": cfg.llm_api_key}
        raw_url = str(cfg.llm_base_url or "").strip()
        if raw_url:
            # Strip trailing slash to avoid double-slash issues with proxies
            client_kwargs["base_url"] = raw_url.rstrip("/")
        self.client = AsyncOpenAI(**client_kwargs)
        self.model = cfg.llm_model
        self.summary_model = getattr(cfg, "llm_summarize_model", cfg.llm_model)
        log(f"DEBUG OpenAICompatibleAgent: initialized with base_url={self.client.base_url} model={self.model}", level="debug")
        self.remember_script = cfg.remember_script
        self.storage = StorageService(build_storage_config(cfg))
        self.messages: list[Any] = []
        self._load_workflow_config()

    def _load_workflow_config(self) -> None:
        workflow_data = load_json_file(WORKFLOW_CONFIG_PATH)
        workflow_data = expand_env_placeholders(workflow_data)
        self.workflow = self._validate_workflow_config(workflow_data)
        log(f"workflow config loaded: {WORKFLOW_CONFIG_PATH}", level="debug")

    def _validate_workflow_config(self, workflow: Any) -> dict[str, Any]:
        workflow_cfg = _require_mapping(workflow, "workflow")
        intents = _require_mapping(workflow_cfg.get("intents"), "intents")
        prompts = _require_mapping(workflow_cfg.get("prompts"), "prompts")
        mcp_servers = workflow_cfg.get("mcp_servers")
        if not isinstance(mcp_servers, dict):
            workflow_cfg["mcp_servers"] = {}
        for key in ("record", "find"):
            _require_mapping(intents.get(key), f"intents.{key}")
            _require_mapping(prompts.get(key), f"prompts.{key}")
        _require_mapping(prompts.get("intent_extractor"), "prompts.intent_extractor")
        _require_mapping(prompts.get("query_normalize"), "prompts.query_normalize")
        # 统一从 prompts.query_rewrite 读取
        _require_mapping(prompts.get("query_rewrite"), "prompts.query_rewrite")
        _require_mapping(prompts.get("memory_translate"), "prompts.memory_translate")
        _require_mapping(prompts.get("remember_summary"), "prompts.remember_summary")
        return workflow_cfg

    def _build_intent_extractor_messages(self, user_input: str) -> list[dict[str, str]]:
        prompts = _require_mapping(self.workflow.get("prompts"), "prompts")
        extractor_cfg = _require_mapping(
            prompts.get("intent_extractor"), "prompts.intent_extractor"
        )
        intent_desc = "\n".join(
            [
                f"- {k}: {v.get('description', '')}"
                for k, v in self.workflow["intents"].items()
            ]
        )
        schema = json.dumps(
            extractor_cfg["schema"], ensure_ascii=False, separators=(",", ":")
        )
        instructions = "\n".join(
            f"{index}. {item}"
            for index, item in enumerate(extractor_cfg["instructions"], start=1)
        )
        # 注入真实的时间基准
        instructions = instructions.replace("${PTT_CURRENT_TIME}", _runtime_current_time_text())
        
        system_prompt = _render_prompt_template(
            str(extractor_cfg["system_prompt"]),
            {
                "INTENT_DESCRIPTIONS": intent_desc,
                "INTENT_EXTRACTION_RULES": instructions,
                "INTENT_JSON_SCHEMA": schema,
            },
        )
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": system_prompt,
            },
        ]
        for example in extractor_cfg["examples"]:
            messages.append({"role": "user", "content": str(example["user"])})
            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        example["assistant"], ensure_ascii=False, separators=(",", ":")
                    ),
                }
            )
        messages.append({"role": "user", "content": user_input})
        return messages

    async def _extract_intent_payload(self, user_input: str) -> dict[str, Any]:
        extract_messages = self._build_intent_extractor_messages(user_input)
        try:
            log_llm_prompt("intent extractor", extract_messages)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=extract_messages,  # type: ignore
                temperature=0,
            )
            finish_reason = str(response.choices[0].finish_reason or "")
            raw_output = str(response.choices[0].message.content or "").strip()
            clean_output = strip_think_tags(raw_output)
            log(
                f"LLM intent response: finish_reason={finish_reason or 'unknown'} "
                f"chars_raw={len(raw_output)} chars_cleaned={len(clean_output)}",
                level="debug"
            )
            log_multiline("LLM intent raw", raw_output)
            log_multiline("LLM intent cleaned", clean_output)
            payload = parse_json_output(clean_output)
            if not isinstance(payload, dict):
                raise RuntimeError("intent extractor did not return a JSON object")
            if "intent" not in payload or "args" not in payload:
                salvaged_payload = salvage_truncated_intent_payload(clean_output)
                if salvaged_payload is not None:
                    log(
                        "LLM intent payload was truncated; salvaged structured fields from partial JSON",
                        level="debug"
                    )
                    payload = salvaged_payload
                else:
                    raise RuntimeError(
                        "intent extractor returned incomplete JSON object"
                    )
            log(
                "LLM intent parsed: "
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                level="info"
            )
            if payload.get("intent") not in ("record", "find"):
                payload["intent"] = "find"
            payload.setdefault("args", {})
            args = payload["args"] if isinstance(payload["args"], dict) else {}
            args.setdefault("memory", "")
            args.setdefault("query", "")
            if payload.get("intent") == "find":
                args["query"] = user_input.strip()
            payload["args"] = args
            if payload.get("intent") == "record":
                payload.setdefault("args", {})
                args = payload["args"] if isinstance(payload["args"], dict) else {}
                task = payload.get("task", {})
                record_task = task.get("record", {}) if isinstance(task, dict) else {}
                if isinstance(record_task, dict):
                    args_memory = str(args.get("memory", "")).strip()
                    task_memory = str(
                        record_task.get("memory", "") or record_task.get("content", "")
                    ).strip()
                    if task_memory and (
                        not args_memory or args_memory in task_memory
                    ):
                        args["memory"] = task_memory
                    else:
                        args["memory"] = args_memory
                args.setdefault("query", "")
                args.setdefault("memory", "")
                args.pop("note", None)
                args.pop("notes", None)
                payload["args"] = args
            return payload
        except Exception as e:
            log(f"Intent extraction failed: {e}", level="error")
        return {
            "intent": "find",
            "tool": "remember_find",
            "args": {"query": user_input.strip()},
            "confidence": 0.0,
        }

    async def classify_intent(self, user_input: str) -> str:
        payload = await self._extract_intent_payload(user_input)
        intent = str(payload.get("intent", "")).strip()
        if intent in self.workflow["intents"]:
            return intent
        return "find"

    def _get_remember_tools(self) -> dict[str, dict[str, Any]]:
        return {
            "remember_add": {
                "type": "function",
                "function": {
                    "name": "remember_add",
                    "description": "Save the user's original words plus one polished memory sentence that preserves every detail.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "memory": {
                                "type": "string",
                                "description": "One polished Chinese memory sentence based on the user's words. It may reorder phrasing or fix obvious transcription errors, but it must not lose or add any detail.",
                            },
                            "original_text": {
                                "type": "string",
                                "description": "The user's original utterance before polishing",
                            },
                        },
                        "required": ["memory"],
                    },
                },
            },
            "remember_find": {
                "type": "function",
                "function": {
                    "name": "remember_find",
                    "description": "Find a remembered fact about an item.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query for the remembered fact",
                            }
                        },
                        "required": ["query"],
                    },
                },
            },
        }

    async def _execute_remember_tool(self, name: str, args: dict) -> str:
        log(
            f"remember tool request: name={name} args={json.dumps(args, ensure_ascii=False)}",
            level="debug"
        )
        remember_store = self.storage.remember_store()
        try:
            if name == "remember_add":
                output = remember_store.add(
                    memory=str(
                        args.get("memory", "")
                        or args.get("content", "")
                        or args.get("location", "")
                    ),
                    original_text=str(args.get("original_text", "")),
                )
            elif name == "remember_find":
                query = str(args.get("query", ""))
                start_date = args.get("start_date")
                end_date = args.get("end_date")
                output = remember_store.find(
                    query=query, 
                    start_date=start_date, 
                    end_date=end_date
                )
            else:
                return f"Error: Unknown tool {name}"
            log(f"remember tool result: {output}", level="debug")
            return output
        except Exception as e:
            return f"Error executing {name}: {e}"

    async def _summarize_remember_output(
        self,
        tool_name: str,
        raw_output: str,
        user_question: str = "",
        query: str = "",
    ) -> str:
        cleaned = raw_output.strip()
        if not cleaned:
            return "没有找到可用结果。"
        if cleaned.startswith("Error:") or cleaned.startswith("Error executing "):
            return cleaned

        extracted_memories: list[str] = []

        # If it's a JSON response, only pass the memory body onward.
        if cleaned.startswith("[") or cleaned.startswith("{"):
            extracted_data = self.storage.remember_store().extract_summary_items(cleaned)
            items = extracted_data.get("items", [])
            for item in items:
                mem_text = str(item.get("memory", "")).strip()
                if mem_text:
                    date_prefix = _memory_date_prefix(
                        str(item.get("updated_at") or item.get("created_at") or "")
                    )
                    if date_prefix:
                        extracted_memories.append(f"- {date_prefix}: {mem_text}")
                    else:
                        extracted_memories.append(f"- {mem_text}")

        # If it's a plain text response (like from remember_add) or JSON failed to provide items
        if not extracted_memories:
            memory_match = re.search(r"(?:📝 记忆|✅ 已记录)[:：]\s*(.+)", cleaned)
            time_match = re.search(r"🕒 时间[:：]\s*([^\n]+)", cleaned)

            memory_value = memory_match.group(1).strip() if memory_match else ""
            created_at = time_match.group(1).strip() if time_match else ""

            if memory_value:
                formatted_time = (
                    format_local_datetime(created_at) if created_at else "刚刚"
                )
                extracted_memories.append(
                    f"- {memory_value} (记录时间: {formatted_time})"
                )
            elif not (cleaned.startswith("[") or cleaned.startswith("{")):
                # Fallback for other plain text
                extracted_memories.append(f"- {cleaned}")

        if not extracted_memories:
            return "没有找到匹配的记忆信息。"

        memories_summary = "\n".join(extracted_memories)

        prompts = _require_mapping(self.workflow.get("prompts"), "prompts")
        remember_summary_cfg = _require_mapping(
            prompts.get("remember_summary"), "prompts.remember_summary"
        )
        system_prompt = str(remember_summary_cfg.get("system_prompt", "")).strip()
        if not system_prompt:
            raise RuntimeError(
                "workflow config missing required section: remember_summary.system_prompt"
            )
        system_prompt = system_prompt.replace(
            "${PTT_CURRENT_TIME}", _runtime_current_time_text()
        )

        user_prompt = (
            f"我的问题：{user_question or query or '（无）'}\n"
            f"命中的记忆原文：\n{memories_summary}"
        )

        try:
            summary_model = str(getattr(self, "summary_model", self.model))
            log(f"DEBUG OpenAICompatibleAgent: summarizing with base_url={self.client.base_url} model={summary_model}", level="debug")
            log_llm_prompt(
                "remember summary",
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            response = await self.client.chat.completions.create(
                model=summary_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
            )
            raw_summary = str(response.choices[0].message.content or "").strip()
            clean_summary = strip_think_tags(raw_summary)
            # Extra safety: remove common JSON-like residue or IDs if LLM hallucinates them
            clean_summary = re.sub(r"[a-f0-9-]{32,}", "", clean_summary)

            log(
                f"remember summary response: chars_raw={len(raw_summary)} chars_cleaned={len(clean_summary)}",
                level="debug"
            )
            log_multiline("remember summary raw", raw_summary)
            log_multiline("remember summary cleaned", clean_summary)
            return clean_summary or "处理完成。"
        except Exception as e:
            log(f"remember output summary failed: {e}", level="error")
            return memories_summary

    async def _execute_structured_tool(
        self, tool_name: str | None, args: dict[str, Any], user_input: str = ""
    ) -> str | None:
        if tool_name is None:
            return None
        log(
            f"structured tool path selected: tool={tool_name} args={json.dumps(args, ensure_ascii=False)}",
            level="debug"
        )
        if tool_name == "remember_add":
            memory = (
                str(args.get("memory", "")).strip()
                or str(args.get("content", "")).strip()
                or user_input.strip()
            )
            if not memory:
                return "Error: structured remember_add missing memory"
            return await self._execute_remember_tool(
                tool_name,
                {
                    "memory": memory,
                    "original_text": user_input.strip(),
                },
            )
        if tool_name == "remember_find":
            query = str(args.get("query", "")).strip()
            start_date = args.get("start_date")
            end_date = args.get("end_date")
            search_query = user_input.strip() or query
            if not search_query and not (start_date or end_date):
                return "Error: structured remember_find missing query or date range"
            raw = await self._execute_remember_tool(
                tool_name, 
                {
                    "query": search_query,
                    "start_date": start_date,
                    "end_date": end_date
                }
            )
            
            # --- 核心修改：非 chat-mode 兜底逻辑 ---
            # 只有在 remember_find 没找到结果时，才需要考虑是否结束
            extracted_data = self.storage.remember_store().extract_summary_items(raw)
            if not extracted_data.get("items"):
                log("OpenAICompatibleAgent: no results found for remember_find", level="info")
                return "没有找到匹配的记忆信息。"
            # ------------------------------------

            return await self._summarize_remember_output(
                tool_name, raw, user_question=user_input, query=search_query
            )
        return None

    async def chat(self, user_input: str) -> str:
        intent_payload = await self._extract_intent_payload(user_input)
        intent_key = str(intent_payload.get("intent", "")).strip()
        
        # 强制归类为 record 或 find
        if intent_key not in ("record", "find"):
            intent_key = "find"

        log(f"Detected intent branch: {intent_key}", level="info")

        tool_name = str(intent_payload.get("tool", "")).strip()
        if tool_name not in {"remember_add", "remember_find"}:
            tool_name = "remember_add" if intent_key == "record" else "remember_find"

        reply = await self._execute_structured_tool(
            tool_name,
            intent_payload.get("args", {}),
            user_input=user_input,
        )

        if reply is not None:
            return reply

        unknown_cfg = self.workflow.get("prompts", {}).get("unknown_intent_reply", {})
        return unknown_cfg.get("text", "无法处理该请求，请尝试换种说法。")
