from __future__ import annotations

import contextlib
import copy
import json
import os
import re
from typing import Any

from press_to_talk.storage import StorageService

from ..models.config import Config
from ..models.history import build_storage_config
from ..utils.env import (
    DEFAULT_WORKFLOW_PATH,
    INTENT_EXTRACTOR_CONFIG_PATH,
    WORKFLOW_CONFIG_PATH,
    expand_env_placeholders,
    load_json_file,
    load_workflow_defaults,
)
from ..utils.logging import log, log_llm_prompt, log_multiline
from ..utils.shell import parse_json_output
from ..utils.text import (
    chat_context_prefix,
    current_time_text,
    format_local_datetime,
    merge_reply_segments,
    preview_text,
    strip_think_tags,
)
from .intent import (
    coerce_to_local_find_payload,
    salvage_truncated_intent_payload,
    wants_explicit_search,
)
from .memory import extract_mem0_summary_payload


def _runtime_current_time_text() -> str:
    try:
        from press_to_talk import core as core_module

        return str(core_module.current_time_text())
    except Exception:
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
        from openai import OpenAI

        client_kwargs: dict[str, Any] = {"api_key": cfg.llm_api_key}
        if cfg.llm_base_url.strip():
            client_kwargs["base_url"] = cfg.llm_base_url.strip()
        self.client = OpenAI(**client_kwargs)
        self.cfg = cfg
        self.model = cfg.llm_model
        self.remember_script = cfg.remember_script
        self.storage = StorageService(build_storage_config(cfg))
        self.messages: list[Any] = []
        self._load_workflow_config()

    def _load_workflow_config(self) -> None:
        defaults = load_workflow_defaults()
        try:
            workflow_data = load_json_file(WORKFLOW_CONFIG_PATH)
            workflow_data = expand_env_placeholders(workflow_data)
            workflow_data = self._inject_runtime_context(workflow_data)
            self.workflow = workflow_data
            log(f"workflow config loaded: {WORKFLOW_CONFIG_PATH}")
        except Exception as e:
            log(f"Failed to load workflow config: {e}")
            self.workflow = defaults
            log(f"workflow config source: {DEFAULT_WORKFLOW_PATH}")

        intents = self.workflow.get("intents")
        if not isinstance(intents, dict) or "chat" not in intents:
            log("workflow config missing valid intents; falling back to defaults")
            self.workflow = defaults
            return

        mcp_servers = self.workflow.get("mcp_servers")
        if not isinstance(mcp_servers, dict):
            self.workflow["mcp_servers"] = {}

    def _inject_runtime_context(self, workflow: dict[str, Any]) -> dict[str, Any]:
        chat_cfg = workflow.get("intents", {}).get("chat")
        if isinstance(chat_cfg, dict):
            system_prompt = str(chat_cfg.get("system_prompt", ""))
            chat_cfg["system_prompt"] = system_prompt.replace(
                "${PTT_CURRENT_TIME}", current_time_text()
            ).replace("${PTT_LOCATION}", "南京")
        return workflow

    def _build_intent_extractor_messages(self, user_input: str) -> list[dict[str, str]]:
        extractor_cfg = load_json_file(INTENT_EXTRACTOR_CONFIG_PATH)
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
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "你是一个中文意图识别与结构化抽取器。"
                    + "请根据用户输入，判断意图，并把要记录、要查找、要联网搜索的内容拆解成 JSON。\n\n"
                    + "意图列表：\n"
                    f"{intent_desc}\n\n"
                    "规则：\n"
                    f"{instructions}\n\n"
                    f"JSON schema:\n{schema}"
                ),
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
            response = self.client.chat.completions.create(
                model=self.model,
                messages=extract_messages,  # type: ignore
                temperature=0,
            )
            finish_reason = str(response.choices[0].finish_reason or "")
            raw_output = str(response.choices[0].message.content or "").strip()
            clean_output = strip_think_tags(raw_output)
            log(
                f"LLM intent response: finish_reason={finish_reason or 'unknown'} "
                f"chars_raw={len(raw_output)} chars_cleaned={len(clean_output)}"
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
                        "LLM intent payload was truncated; salvaged structured fields from partial JSON"
                    )
                    payload = salvaged_payload
                else:
                    raise RuntimeError(
                        "intent extractor returned incomplete JSON object"
                    )
            log(
                "LLM intent parsed: "
                + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
            if wants_explicit_search(user_input):
                return coerce_to_local_find_payload(
                    user_input, payload, note="联网搜索请求已并入本地查询"
                )
            if payload.get("intent") == "search":
                payload = coerce_to_local_find_payload(
                    user_input, payload, note="search 已并入本地查询"
                )
            if payload.get("intent") == "chat":
                payload = coerce_to_local_find_payload(
                    user_input, payload, note="chat 已并入本地查询"
                )
            payload.setdefault("args", {})
            args = payload["args"] if isinstance(payload["args"], dict) else {}
            args.setdefault("memory", "")
            args.setdefault("query", "")
            args.setdefault("note", "")
            if payload.get("intent") == "find":
                args["query"] = user_input.strip()
            payload["args"] = args
            if payload.get("intent") == "record":
                payload.setdefault("args", {})
                args = payload["args"] if isinstance(payload["args"], dict) else {}
                task = payload.get("task", {})
                record_task = task.get("record", {}) if isinstance(task, dict) else {}
                if isinstance(record_task, dict):
                    args["memory"] = str(
                        args.get("memory", "")
                        or record_task.get("memory", "")
                        or record_task.get("content", "")
                    ).strip()
                    args["note"] = str(
                        args.get("note", "")
                        or args.get("notes", "")
                        or record_task.get("note", "")
                        or record_task.get("notes", "")
                    ).strip()
                else:
                    args["note"] = str(
                        args.get("note", "") or args.get("notes", "")
                    ).strip()
                args.setdefault("query", "")
                args.setdefault("memory", "")
                payload["args"] = args
            return payload
        except Exception as e:
            log(f"Intent extraction failed: {e}")
        return coerce_to_local_find_payload(
            user_input,
            {
                "confidence": 0.0,
            },
            note="结构化提取失败，回退本地查询",
        )

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
                    "description": "Save one concise remembered sentence distilled from the user's statement.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "memory": {
                                "type": "string",
                                "description": "One concise remembered sentence in Chinese, such as 用户安装了显示器的增高板 or 伊朗和美国停战两周",
                            },
                            "original_text": {
                                "type": "string",
                                "description": "Original user utterance before summarization",
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
            f"remember tool request: name={name} args={json.dumps(args, ensure_ascii=False)}"
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
                output = remember_store.find(query=str(args["query"]))
            else:
                return f"Error: Unknown tool {name}"
            log(f"remember tool result: {output}")
            return output
        except Exception as e:
            return f"Error executing {name}: {e}"

    def _summarize_remember_output(
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
            extracted_data = extract_mem0_summary_payload(cleaned)
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

        remember_summary_cfg = self.workflow.get("remember_summary", {})
        system_prompt = str(remember_summary_cfg.get("system_prompt", "")).strip()
        if not system_prompt:
            system_prompt = (
                "你是一个智能助手。"
                f"今天是 {_runtime_current_time_text()}。"
                "你收到了我询问的问题，以及从数据库中筛选出的记忆列表。"
                "你需要只基于这些结果，用友好简短的方式直接回答我。"
                "绝对不要提及记录 ID、技术字段、分数或内部术语。\n"
                "规则：\n"
                "1. 用“我”和“你”来指代，不要说“用户”，可以称呼我为“大王”。\n"
                "2. 结合记录时间，用人类习惯的口语回答（如：‘你在周一记过……’）。\n"
                "3. 回复要简练，直接进入主题。"
            )
        else:
            system_prompt = system_prompt.replace(
                "${PTT_CURRENT_TIME}", _runtime_current_time_text()
            )

        user_prompt = (
            f"我的问题：{user_question or query or '（无）'}\n"
            f"命中的记忆原文：\n{memories_summary}"
        )

        try:
            log_llm_prompt(
                "remember summary",
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            response = self.client.chat.completions.create(
                model=self.model,
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
                f"remember summary response: chars_raw={len(raw_summary)} chars_cleaned={len(clean_summary)}"
            )
            log_multiline("remember summary raw", raw_summary)
            log_multiline("remember summary cleaned", clean_summary)
            return clean_summary or "处理完成。"
        except Exception as e:
            log(f"remember output summary failed: {e}")
            return memories_summary

    async def _execute_structured_tool(
        self, tool_name: str | None, args: dict[str, Any], user_input: str = ""
    ) -> str | None:
        if tool_name is None:
            return None
        log(
            f"structured tool path selected: tool={tool_name} args={json.dumps(args, ensure_ascii=False)}"
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
            search_query = user_input.strip() or query
            if not search_query:
                return "Error: structured remember_find missing query"
            raw = await self._execute_remember_tool(tool_name, {"query": search_query})
            return self._summarize_remember_output(
                tool_name, raw, user_question=user_input, query=search_query
            )
        return None

    async def chat(self, user_input: str) -> str:
        intent_payload = await self._extract_intent_payload(user_input)
        intent_key = str(intent_payload.get("intent", "")).strip()
        if intent_key not in self.workflow.get("intents", {}):
            intent_key = "find"
        log(f"Detected intent branch: [bold cyan]{intent_key}[/bold cyan]")

        intents = self.workflow.get("intents", {})
        intent_cfg = (
            intents.get(intent_key) or intents.get("find") or intents.get("chat")
        )
        if not intent_cfg:
            raise RuntimeError("workflow config does not contain a usable chat intent")
        intent_cfg = copy.deepcopy(intent_cfg)
        log(
            "active system prompt: "
            + preview_text(str(intent_cfg.get("system_prompt", "")), limit=160)
        )
        structured_tool_result = await self._execute_structured_tool(
            intent_payload.get("tool"),
            intent_payload.get("args", {}),
            user_input=user_input,
        )
        if structured_tool_result is not None:
            return structured_tool_result

        # Prepare branch-specific context in a fresh, local message list.
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": f"{chat_context_prefix()}\n{intent_cfg['system_prompt']}",
            },
            {"role": "user", "content": user_input},
        ]

        async with contextlib.AsyncExitStack() as stack:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            sessions: dict[str, Any] = {}
            active_mcp = set()

            # Filter relevant MCP servers based on selected intent tools
            for t_name in intent_cfg.get("tools", []):
                if "___" in t_name:
                    active_mcp.add(t_name.split("___")[0])

            for name in active_mcp:
                if name in self.workflow["mcp_servers"]:
                    config = self.workflow["mcp_servers"][name]
                    env = os.environ.copy()
                    if "env" in config:
                        env.update(config["env"])
                    server_params = StdioServerParameters(
                        command=config["command"], args=config["args"], env=env
                    )
                    try:
                        read, write = await stack.enter_async_context(
                            stdio_client(server_params)
                        )
                        session = await stack.enter_async_context(
                            ClientSession(read, write)
                        )
                        await session.initialize()
                        sessions[name] = session
                    except Exception as e:
                        log(f"Failed to initialize MCP server {name}: {e}")

            # Collect tools for this specific branch
            tools: list[Any] = []
            remember_tools = self._get_remember_tools()
            for tool_name in intent_cfg.get("tools", []):
                tool_spec = remember_tools.get(tool_name)
                if tool_spec is not None:
                    tools.append(tool_spec)

            for name, session in sessions.items():
                try:
                    mcp_tools = await session.list_tools()
                    for t in mcp_tools.tools:
                        full_name = f"{name}___{t.name}"
                        configured_tools = set(intent_cfg.get("tools", []))
                        aliases = {full_name}
                        if name == "brave-search":
                            aliases.add("brave-search___search")
                            aliases.add("brave-search___brave-search")
                        if aliases & configured_tools:
                            tools.append(
                                {
                                    "type": "function",
                                    "function": {
                                        "name": full_name,
                                        "description": t.description,
                                        "parameters": t.inputSchema,
                                    },
                                }
                            )
                except Exception as e:
                    log(f"Failed to list tools for {name}: {e}")

            log(
                f"active tools for intent {intent_key}: "
                + ", ".join(
                    tool["function"]["name"] for tool in tools if "function" in tool
                )
                if tools
                else f"active tools for intent {intent_key}: none"
            )
            if intent_key == "search" and not tools:
                raise RuntimeError(
                    "search intent has no available MCP tools; check brave-search/fetch server startup"
                )

            reply_segments: list[str] = []
            continuation_requests = 0
            for iteration in range(7):
                try:
                    # Construct parameters dynamically to avoid API errors with tool_choice
                    api_params: dict[str, Any] = {
                        "model": self.model,
                        "messages": messages,  # type: ignore
                    }
                    if tools:
                        api_params["tools"] = tools
                        api_params["tool_choice"] = "auto"

                    log(
                        f"calling LLM iteration={iteration + 1} intent={intent_key} "
                        f"messages={len(messages)} tools={len(tools)}"
                    )
                    log_llm_prompt(f"chat/{intent_key}", messages)
                    response = self.client.chat.completions.create(**api_params)
                except Exception as e:
                    return f"Error calling LLM: {e}"

                resp_message = response.choices[0].message
                messages.append(resp_message.model_dump(exclude_none=True))
                finish_reason = str(response.choices[0].finish_reason or "")
                tool_call_count = len(resp_message.tool_calls or [])
                raw_resp_content = str(resp_message.content or "").strip()
                clean_resp_content = strip_think_tags(raw_resp_content)
                log(
                    f"LLM response meta: tool_calls={tool_call_count} "
                    f"finish_reason={finish_reason or 'unknown'} "
                    f"chars_raw={len(raw_resp_content)} chars_cleaned={len(clean_resp_content)}"
                )
                log_multiline("LLM response raw", raw_resp_content)
                log_multiline("LLM response cleaned", clean_resp_content)

                if not resp_message.tool_calls:
                    raw_reply = raw_resp_content
                    reply_part = strip_think_tags(raw_reply)
                    if reply_part:
                        reply_segments.append(reply_part)
                    reply = merge_reply_segments(reply_segments)
                    if not reply and not reply_part:
                        log("LLM produced no tool call and no usable text reply")
                    if finish_reason == "length" and continuation_requests < 2:
                        continuation_requests += 1
                        log(
                            f"LLM reply truncated by length; requesting continuation pass {continuation_requests}"
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "继续上一个回答，从刚才没说完的地方接着说完。"
                                    "不要重复前文，不要输出 <think>，直接续写完整。"
                                ),
                            }
                        )
                        continue
                    return reply

                for tool_call in resp_message.tool_calls:
                    func_name = tool_call.function.name
                    func_args = json.loads(tool_call.function.arguments)
                    log(
                        f"LLM tool call: name={func_name} args={tool_call.function.arguments}"
                    )

                    tool_result = ""
                    if func_name.startswith("remember_"):
                        tool_result = await self._execute_remember_tool(
                            func_name, func_args
                        )
                    elif "___" in func_name:
                        server_name, actual_tool_name = func_name.split("___", 1)
                        if server_name in sessions:
                            try:
                                log(
                                    f"Calling MCP tool: {actual_tool_name} on {server_name}"
                                )
                                result = await sessions[server_name].call_tool(
                                    actual_tool_name, func_args
                                )
                                tool_result = "\n".join(
                                    c.text for c in result.content if c.type == "text"
                                )  # type: ignore
                            except Exception as e:
                                tool_result = f"Error calling {actual_tool_name}: {e}"
                        else:
                            tool_result = (
                                f"Error: MCP server {server_name} not available"
                            )
                    else:
                        tool_result = f"Error: Unknown tool format {func_name}"

                    messages.append(
                        {
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": func_name,
                            "content": tool_result,
                        }
                    )

            return "Error: Too many tool call iterations."
