"""Fast-path handler for record/ask chat.

Pipeline:
  1. TypeSafe once: ask_intent_and_delivery(query, selected_text)
     - intent: "record" | "other"
     - delivery: "paste" | "speak" | None（期望产出是粘贴内容还是播报回答）
  2. record -> ChatCompletion semantic summary -> Memos REST API insertion
  3. other + delivery=paste -> Harness(chat-fast) 按"指令 + 可选选中文本"产出
     最终内容（改写/生成），调用方直接粘贴到光标处（action="paste"）
  4. other + 其他（询问）:
     a. Harness(chat-fast) 拆词 (keywords JSON)
     b. 一次 Memos CEL 查询 (~0.1-0.3s)，异常/空 = 无上下文
     c. Harness(chat-fast) 带上下文回答（无匹配则正常闲聊，可 web_search）

Total target: ≤ 8 s end-to-end for the /v1/chat endpoint.
All prompts are strictly loaded from external workflow_config.json.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
import json
import os
import re
import time
from typing import Any

last_typesafe_debug: ContextVar[dict[str, Any] | None] = ContextVar("last_typesafe_debug", default=None)

from ..harness import DeepSeekHarnessClient
from .reply_stream import complete_reply, has_partial_reply, watch_prompt
from ..storage.providers.memos import (
    MemosClient,
    _strip_tags_and_voice_prefix,
)
from ..utils.env import load_workflow_config
from ..utils.logging import log
from ..utils.text import current_time_with_weekday_text
from ..utils.typesafe import ask_intent_and_delivery

_MAX_SELECTION_CHARS = 20000


# ---------------------------------------------------------------------------
# Harness helpers (拆词 / 回答，均走 chat-fast 一次性客户端)
# ---------------------------------------------------------------------------

def _chat_harness_client() -> DeepSeekHarnessClient:
    """Build a disposable one-shot chat-fast client so calls share no context."""
    timeout = float(
        os.environ.get("PTT_CHAT_TIMEOUT_SECONDS")
        or os.environ.get("PTT_HARNESS_TIMEOUT_SECONDS")
        or 30
    )
    return DeepSeekHarnessClient.from_env(
        agent_preset=os.environ.get("PTT_CHAT_HARNESS_AGENT_PRESET", "chat-fast"),
        timeout_seconds=timeout,
    )


def _parse_keywords_json(text: str) -> list[str]:
    """Parse JSON object or bare array of keywords from Harness reply."""
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                kws = data.get("keywords")
                if isinstance(kws, list):
                    return [str(k).strip() for k in kws if str(k).strip()]
        except Exception:
            pass
    match_arr = re.search(r"\[.*\]", raw, re.DOTALL)
    if match_arr:
        try:
            data = json.loads(match_arr.group(0))
            if isinstance(data, list):
                return [str(k).strip() for k in data if str(k).strip()]
        except Exception:
            pass
    return []


def _prompt(template_key: str) -> str:
    cfg = load_workflow_config()
    return str((cfg.get("prompts", {}).get(template_key) or {}).get("system_prompt", ""))


async def _extract_keywords_with_harness(query: str, selection: str = "") -> list[str]:
    """Harness(chat-fast) 拆词：从问句（及可选选中文本上下文）提炼检索关键词。失败返回空列表。"""
    query_text = query
    if selection.strip() and len(query.strip()) <= 15:
        # 问句极短或含代词时，附带截取的部分选中文本帮助精准提取实体
        query_text = f"{query} （参考选中文本：{selection[:200]}）"

    prompt = _prompt("harness_keyword_extract")
    current_time = current_time_with_weekday_text()
    if not prompt:
        prompt = (
            "从下面这句用户问句中提炼 2 到 5 个最核心、最可能命中个人备忘的检索词"
            "（人名、物品、地点、事件等具体实体）。只返回 JSON："
            "{\"keywords\":[\"词1\",\"词2\"]}\n\n用户问句：%s" % query_text
        )
    else:
        prompt = (
            prompt.replace("%%CURRENT_TIME%%", current_time)
            .replace("${PTT_CURRENT_TIME}", current_time)
            .replace("%%QUERY%%", query_text)
        )

    client = _chat_harness_client()
    try:
        result = await client.query(prompt)
        keywords = _parse_keywords_json(result.get("reply", ""))
        log(f"fast-chat: harness 拆词 -> {keywords}", level="info")
        return keywords
    except Exception as exc:
        log(f"fast-chat: harness 拆词失败: {type(exc).__name__}: {exc}", level="warn")
        return []
    finally:
        await client.close()


async def _answer_with_harness(query: str, memos: list[dict[str, Any]], selection: str = "") -> str:
    """Harness(chat-fast) 回答：有匹配备忘则基于备忘回答，有选中文本则作为上下文，无则正常闲聊。"""
    prompt = _prompt("harness_answer")
    current_time = current_time_with_weekday_text()
    memo_lines = [str(m.get("memory", "")).strip() for m in memos if str(m.get("memory", "")).strip()]
    memos_block = "\n".join(memo_lines) or "（无相关备忘）"
    selection_block = selection.strip() or "（无选中文本）"
    if not prompt:
        prompt = (
            "你是语音助手的最终回答链路。当前时间：%%CURRENT_TIME%%。人称准则：直接对用户说话，在回答中涉及用户的行为时，严禁使用“用户”一词，必须一律改用“你”或“您”。\n\n"
            "回答规范：\n"
            "1. 如果用户当前有选中的文本（上下文），必须将其作为核心上下文，紧密结合选中文本回答用户问题（如解释、总结、答疑等）。\n"
            "2. 优先参考下面提供的相关备忘内容回答用户问题，不要提及“检索”“备忘”等内部机制。\n"
            "3. 如果相关备忘不足或没有，直接根据你的知识和联网检索能力回答（例如天气、常识、闲聊等），不要回复“信息不足”。\n"
            "4. 如果回答涉及多条记录、多个事项，必须使用 Markdown 无序列表（- ）分行陈列。\n"
            "5. 回答保持简短、直接、适合语音播报。\n\n"
            "用户问题：%%QUERY%%\n\n"
            "用户当前选中的文本（上下文）：\n%%SELECTION%%\n\n"
            "相关备忘：\n%%MEMOS%%"
        )
    prompt = (
        prompt.replace("%%CURRENT_TIME%%", current_time)
        .replace("${PTT_CURRENT_TIME}", current_time)
        .replace("%%QUERY%%", query)
        .replace("%%MEMOS%%", memos_block)
        .replace("%%SELECTION%%", selection_block)
    )
    if current_time not in prompt:
        prompt = f"当前时间：{current_time}\n\n{prompt}"
    if selection.strip() and "%%SELECTION%%" not in _prompt("harness_answer") and selection_block not in prompt:
        prompt += f"\n\n【用户当前选中的文本（上下文）】\n{selection.strip()}"

    client = _chat_harness_client()
    try:
        result = await client.query(prompt + ("\n\n" + watch_prompt() if watch_prompt() else ""))
        reply = str(result.get("reply", "")).strip()
        reasoning = str(result.get("reasoning", "") or "").strip()
        from ..harness.client import HarnessReply
        return HarnessReply(reply, reasoning)
    finally:
        await client.close()


async def _compose_with_harness(query: str, selection: str) -> str | None:
    """Harness(chat-fast) 产出要粘贴的最终内容（改写/生成）。失败返回 None。"""
    selection_block = selection or "（无选中文本，按指令直接生成）"
    prompt = _prompt("harness_compose")
    current_time = current_time_with_weekday_text()
    if not prompt:
        prompt = (
            "你是一个文本产出器。当前时间：%%CURRENT_TIME%%。根据用户指令直接输出要替换选中文本、或粘贴到光标处的最终内容本身，"
            "不要任何解释、前缀或代码围栏。若提供了选中文本且指令是对它的加工，"
            "以选中文本为基础完成。\n\n用户指令：%s\n\n用户当前选中的文本（可能为空）：\n%s"
            % (query, selection_block)
        )
    prompt = (
        prompt.replace("%%CURRENT_TIME%%", current_time)
        .replace("${PTT_CURRENT_TIME}", current_time)
        .replace("%%INSTRUCTION%%", query)
        .replace("%%SELECTION%%", selection_block)
    )
    if current_time not in prompt:
        prompt = f"当前时间：{current_time}\n\n{prompt}"

    client = _chat_harness_client()
    try:
        result = await client.query(prompt + ("\n\n" + watch_prompt() if watch_prompt() else ""))
        reply = str(result.get("reply", "")).strip()
        reasoning = str(result.get("reasoning", "") or "").strip()
        log(f"fast-chat: harness 产出粘贴内容 {len(reply)} 字", level="info")
        if not reply:
            return None
        from ..harness.client import HarnessReply
        return HarnessReply(reply, reasoning)
    except Exception as exc:
        log(f"fast-chat: harness 产出内容失败: {type(exc).__name__}: {exc}", level="warn")
        return None
    finally:
        await client.close()


async def _answer_with_direct_llm(
    query: str,
    memos: list[dict[str, Any]] | None = None,
    selection: str = "",
) -> Any | None:
    """直接调用 fast 模型 completion（直连 cliproxy），跳过 DSH Agent 轮询。失败返回 None。"""
    try:
        from openai import AsyncOpenAI
        from ..harness.client import HarnessReply

        base_url = (os.environ.get("OPENAI_BASE_URL") or "http://cliproxy.docker.home/v1").rstrip("/")
        api_key = os.environ.get("OPENAI_API_KEY") or "sk-1234"
        model = os.environ.get("PTT_MODEL") or "fast"

        prompt = _prompt("harness_answer")
        current_time = current_time_with_weekday_text()
        memo_lines = [str(m.get("memory", "")).strip() for m in (memos or []) if str(m.get("memory", "")).strip()]
        memos_block = "\n".join(memo_lines) or "（无相关备忘）"
        selection_block = selection.strip() or "（无选中文本）"
        if not prompt:
            prompt = (
                "你是语音助手的最终回答链路。当前时间：%%CURRENT_TIME%%。人称准则：直接对用户说话，严禁使用“用户”一词，必须一律改用“你”或“您”。\n\n"
                "用户问题：%%QUERY%%\n\n"
                "用户当前选中的文本（上下文）：\n%%SELECTION%%\n\n"
                "相关备忘：\n%%MEMOS%%"
            )
        prompt = (
            prompt.replace("%%CURRENT_TIME%%", current_time)
            .replace("${PTT_CURRENT_TIME}", current_time)
            .replace("%%QUERY%%", query)
            .replace("%%MEMOS%%", memos_block)
            .replace("%%SELECTION%%", selection_block)
        )
        if current_time not in prompt:
            prompt = f"当前时间：{current_time}\n\n{prompt}"
        if selection.strip() and "%%SELECTION%%" not in _prompt("harness_answer") and selection_block not in prompt:
            prompt += f"\n\n【用户当前选中的文本（上下文）】\n{selection.strip()}"

        client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=12.0)
        messages = [
            {"role": "system", "content": "你是个人语音助手。直接对用户说话，涉及用户时称“你”或“您”，不提UTC，回答自然简短。"},
            {"role": "user", "content": prompt},
        ]
        try:
            reply = await complete_reply(client, model=model, messages=messages)
        finally:
            await client.close()
        if reply:
            log(f"fast-chat: direct llm({model}) answered {len(reply)} chars", level="info")
            return reply
        return None
    except Exception as exc:
        if has_partial_reply():
            raise
        log(f"fast-chat: direct llm failed ({exc}), falling back to harness", level="info")
        return None


async def _compose_with_direct_llm(query: str, selection: str) -> Any | None:
    """直接调用 fast 模型产出要回贴的内容，跳过 DSH Agent 轮询。失败返回 None。"""
    try:
        from openai import AsyncOpenAI
        from ..harness.client import HarnessReply

        base_url = (os.environ.get("OPENAI_BASE_URL") or "http://cliproxy.docker.home/v1").rstrip("/")
        api_key = os.environ.get("OPENAI_API_KEY") or "sk-1234"
        model = os.environ.get("PTT_MODEL") or "fast"

        selection_block = selection or "（无选中文本，按指令直接生成）"
        prompt = _prompt("harness_compose")
        current_time = current_time_with_weekday_text()
        if not prompt:
            prompt = (
                "你是一个文本产出器。当前时间：%%CURRENT_TIME%%。根据用户指令直接输出要替换选中文本、或粘贴到光标处的最终内容本身，"
                "不要任何解释、前缀或代码围栏。\n\n用户指令：%s\n\n用户当前选中的文本（可能为空）：\n%s"
                % (query, selection_block)
            )
        prompt = (
            prompt.replace("%%CURRENT_TIME%%", current_time)
            .replace("${PTT_CURRENT_TIME}", current_time)
            .replace("%%INSTRUCTION%%", query)
            .replace("%%SELECTION%%", selection_block)
        )
        if current_time not in prompt:
            prompt = f"当前时间：{current_time}\n\n{prompt}"

        client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=12.0)
        messages = [
            {"role": "system", "content": "你是文本产出器。只输出最终内容，不要任何解释。"},
            {"role": "user", "content": prompt},
        ]
        try:
            reply = await complete_reply(client, model=model, messages=messages)
        finally:
            await client.close()
        if reply:
            log(f"fast-chat: direct llm({model}) composed {len(reply)} chars", level="info")
            return reply
        return None
    except Exception as exc:
        if has_partial_reply():
            raise
        log(f"fast-chat: direct llm compose failed ({exc}), falling back to harness", level="info")
        return None


# ---------------------------------------------------------------------------
# Memos helpers
# ---------------------------------------------------------------------------

def _build_memos_client() -> MemosClient:
    """Build a MemosClient from environment variables or workflow config."""
    cfg = load_workflow_config().get("memos", {})
    base_url = (
        os.environ.get("MEMOS_BASE_URL")
        or os.environ.get("MEMOS_API_URL")
        or cfg.get("base_url")
        or "http://ds.home:5230"
    )
    token = (
        os.environ.get("MEMOS_TOKEN")
        or os.environ.get("MEMOS_ACCESS_TOKEN")
        or cfg.get("access_token")
        or "memos_pat_voice_assistant_lan_2026"
    )
    timeout = float(os.environ.get("MEMOS_TIMEOUT", "5"))
    return MemosClient(base_url=base_url, token=token, timeout=timeout)


async def _summarize_record_content(query: str, selection: str = "") -> str:
    """Use a single ChatCompletion to prepare memo text; never clean by keywords."""
    from openai import AsyncOpenAI

    prompt = _prompt("memo_record_summary")
    if not prompt:
        raise ValueError("memo_record_summary prompt is missing")
    model = os.environ.get("PTT_MODEL") or "fast"
    async with AsyncOpenAI(
        base_url=(os.environ.get("OPENAI_BASE_URL") or "http://cliproxy.docker.home/v1").rstrip("/"),
        api_key=os.environ.get("OPENAI_API_KEY") or "sk-1234",
        timeout=12.0,
        max_retries=0,
    ) as client:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps({
                    "original_text": query,
                    "selected_text": selection,
                    "current_time": current_time_with_weekday_text(),
                }, ensure_ascii=False)},
            ],
            max_tokens=4096,
        )
    if not response.choices or response.choices[0].finish_reason != "stop":
        raise ValueError("memo summary completion is incomplete")
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise ValueError("memo summary completion is empty")
    log(f"fast-chat: record summary model={model} chars={len(content)}", level="info")
    return content


def _record_memo(client: MemosClient, content: str, original_text: str) -> str:
    """Create a new memo with voice tag in Memos."""
    parts = [content]
    if original_text.strip() and original_text.strip() != content:
        parts.append(f"> 语音原文: {original_text.strip()}")
    parts.append("#voice")
    full_content = "\n\n".join(parts)
    res = client.create_memo(full_content, visibility="PRIVATE")
    log(f"fast-chat: memo created: {res.get('name')}", level="info")
    return f"✅ 已记入 Memos：{content}"


def _search_memos_cel(client: MemosClient, keywords: list[str]) -> list[dict[str, Any]]:
    """单次 CEL 查询：content.contains('词1') || content.contains('词2')。

    异常/空结果一律视为"无上下文"（返回 []）——新方案里 CEL 失败 = 没有相关备忘 = 继续闲聊，
    不再做 fallback 翻页/重试/不可用异常。
    """
    cfg = load_workflow_config().get("memos", {})
    query_timeout = float(os.environ.get("MEMOS_QUERY_TIMEOUT", cfg.get("query_timeout_seconds", 1.5)))

    valid_words = [
        w.strip() for w in keywords
        if w.strip() and not any(c in w for c in ("'", '"', "\\", "\n", "\r"))
    ]
    if not valid_words:
        return []

    filter_expr = " || ".join(f"content.contains('{w}')" for w in valid_words)
    log(f"fast-chat: CEL query expr: {filter_expr}", level="info")
    try:
        res = client.list_memos(
            page_size=10,
            filter_expr=filter_expr,
            timeout=query_timeout,
        )
        raw_memos = res.get("memos", []) or []
    except Exception as exc:
        log(f"fast-chat: CEL query failed（视为无上下文）: {exc}", level="warn")
        raw_memos = []

    items: list[dict[str, Any]] = []
    for memo in raw_memos:
        content = str(memo.get("content", "")).strip()
        if not content:
            continue
        clean_memory = _strip_tags_and_voice_prefix(content)
        if not clean_memory:
            continue
        created = str(memo.get("createTime", "")).strip()
        items.append({
            "id": str(memo.get("name", "")),
            "memory": clean_memory,
            "raw_content": content,
            "created_at": created,
            "updated_at": str(memo.get("updateTime", "") or created).strip(),
        })
    return items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def try_fast_memory_chat(
    query: str,
    *,
    stream_callback: Any | None = None,
    selected_text: str | None = None,
) -> dict[str, Any] | None:
    """Attempt a fast-path memory operation.

    Returns a dict with ``reply``, ``action`` ("speak"|"paste"), ``memories``,
    ``query``, ``debug_info`` on success, or *None* if the fast path cannot
    serve this query (TypeSafe disabled/failed or 询问链路异常——caller falls
    back to Harness).
    """
    t0 = time.monotonic()
    last_typesafe_debug.set(None)
    selection = (selected_text or "").strip()
    if len(selection) > _MAX_SELECTION_CHARS:
        log(f"fast-chat: 选中文本过长（{len(selection)} 字符），截断至 {_MAX_SELECTION_CHARS}", level="info")
        selection = selection[:_MAX_SELECTION_CHARS]
    log(f"fast-chat: 收到查询 query={query[:80]} selection_len={len(selection)}", level="info")

    # -- Step 1: TypeSafe 一次调用，二分"记录 / 其他（询问）"+ 期望产出方式 --
    t_ts = time.monotonic()
    try:
        decision = await asyncio.to_thread(ask_intent_and_delivery, query, selection or None, return_details=True)
    except TypeError:
        decision = await asyncio.to_thread(ask_intent_and_delivery, query, selection or None)
    elapsed_ts = time.monotonic() - t_ts
    if decision is None:
        log(f"fast-chat: TypeSafe 未启用或调用失败，交由 Harness Agent 回退", level="info")
        return None
    intent = decision["intent"]
    delivery = decision.get("delivery")
    details = decision.get("details") or {}
    log(f"fast-chat: typesafe intent={intent} delivery={delivery} in {elapsed_ts:.2f}s", level="info")

    rule_override = None
    # 显式网络搜索与时效性查询嗅探：明确要求联网搜索时，转交具备 FreeSerp 搜索工具的 Agent
    search_keywords = ("查网络", "查一下网络", "搜索网络", "搜一下网络", "上网查", "联网搜索", "百度一下", "谷歌一下", "全网搜索", "查下网络", "搜索一下")
    has_memo_kw = any(k in query for k in ("备忘", "memo", "记忆"))
    if not has_memo_kw and any(kw in query for kw in search_keywords):
        log(f"fast-chat: 触发显式网络搜索规则，转向 Agent (chat-fast) 处理 query={query}", level="info")
        intent = "agent"
        rule_override = "显式网络搜索关键词命中，强制转交 Agent"

    typesafe_debug = {
        "model": details.get("model") or "clef-flash",
        "elapsed_s": round(elapsed_ts, 3),
        "intent": details.get("intent") or {"choice": intent},
        "delivery": details.get("delivery") or {"choice": delivery},
    }
    if rule_override:
        typesafe_debug["rule_override"] = rule_override
        typesafe_debug["final_intent"] = "agent"

    last_typesafe_debug.set(typesafe_debug)

    try:
        memos_client = _build_memos_client()
    except Exception as exc:
        log(f"fast-chat: cannot build Memos client: {exc}", level="warn")
        return None

    # -- RECORD：语义整理后写 Memos，失败不回退到关键词清洗或原文直写 --
    if intent == "record":
        stage = "record_summary"
        t_summary = time.monotonic()
        try:
            content = await _summarize_record_content(query, selection)
            summary_s = time.monotonic() - t_summary
            stage = "record_memo"
            result_text = await asyncio.to_thread(
                _record_memo, memos_client, content, query,
            )
        except Exception as exc:
            err_type = type(exc).__name__
            log(f"fast-chat [STAGE: {stage}] failed: {err_type}: {exc}", level="error")
            return {
                "reply": "记录失败，请稍后再试。",
                "action": "speak",
                "memories": [],
                "query": query,
                "debug_info": {
                    "backend": "fast-chat",
                    "intent": "record",
                    "stage": stage,
                    "error_type": err_type,
                    "error_detail": str(exc) or err_type,
                    "typesafe": typesafe_debug,
                    "typesafe_s": round(elapsed_ts, 2),
                },
            }
        elapsed_total = time.monotonic() - t0
        log(f"fast-chat: record done in {elapsed_total:.2f}s", level="info")
        return {
            "reply": result_text,
            "action": "speak",
            "memories": [],
            "query": query,
            "debug_info": {
                "backend": "fast-chat",
                "intent": "record",
                "elapsed_s": round(elapsed_total, 2),
                "typesafe_s": round(elapsed_ts, 2),
                "typesafe": typesafe_debug,
                "summary_s": round(summary_s, 2),
            },
        }

    # -- AGENT：复杂任务或需要工具执行，直接交由慢路径 Harness Agent 处理 --
    if intent == "agent":
        log("fast-chat: clef 判定为复杂任务/需要外部工具，直接交由 DSH Agent 状态机处理", level="info")
        return None

    # -- OTHER + PASTE：按"指令 + 可选选中文本"产出内容，调用方回贴 --
    if delivery == "paste":
        try:
            stage = "compose"
            t_comp = time.monotonic()
            # 优先尝试直连 fast LLM 极速产出文本
            reply = await _compose_with_direct_llm(query, selection)
            if not reply:
                reply = await _compose_with_harness(query, selection)
            elapsed_comp = time.monotonic() - t_comp
            if not reply:
                log("fast-chat: compose 产出为空，降级为播报", level="warn")
                return {
                    "reply": "内容生成失败，请稍后再试。",
                    "action": "speak",
                    "memories": [],
                    "query": query,
                    "debug_info": {
                        "backend": "fast-chat",
                        "intent": "compose",
                        "stage": "compose_empty",
                        "typesafe": typesafe_debug,
                        "typesafe_s": round(elapsed_ts, 2),
                    },
                }
            elapsed_total = time.monotonic() - t0
            log(f"fast-chat: compose done in {elapsed_total:.2f}s", level="info")
            reasoning = getattr(reply, "reasoning", None) or None
            return {
                "reply": reply,
                "action": "paste",
                "reasoning": reasoning,
                "memories": [],
                "query": query,
                "debug_info": {
                    "backend": "fast-chat",
                    "intent": "compose",
                    "has_selection": bool(selection),
                    "reasoning": reasoning,
                    "elapsed_s": round(elapsed_total, 2),
                    "typesafe_s": round(elapsed_ts, 2),
                    "typesafe": typesafe_debug,
                    "elapsed_compose_s": round(elapsed_comp, 2),
                },
            }
        except Exception as exc:
            if has_partial_reply():
                raise
            import traceback
            tb = traceback.format_exc()
            log(
                f"fast-chat [STAGE: COMPOSE_ERROR] 产出链路失败: "
                f"{type(exc).__name__}: {exc}\n{tb}",
                level="error",
            )
            return None

    # -- CHAT / SIMPLE：简单问题直接直通 fast LLM，跳过 DSH Agent 状态机，不检索 Memos --
    if intent in ("chat", "simple"):
        stage = "direct_llm_answer"
        t_ans = time.monotonic()
        reply = await _answer_with_direct_llm(query, selection=selection)
        backend_name = "fast-chat-direct-llm"
        if not reply:
            backend_name = "fast-chat-harness-fallback"
            try:
                try:
                    reply = await _answer_with_harness(query, [], selection=selection)
                except TypeError:
                    reply = await _answer_with_harness(query, [])
            except Exception as exc:
                log(
                    f"fast-chat [STAGE: CHAT_ANSWER_ERROR] {type(exc).__name__}: {exc}",
                    level="error",
                )
                return None
        elapsed_ans = time.monotonic() - t_ans
        reply_str = str(reply or "") if has_partial_reply() else str(reply or "").strip()
        if not reply_str:
            reply_str = "大王，这个问题我暂时没有好的答案。"
        elapsed_total = time.monotonic() - t0
        log(f"fast-chat: {backend_name} answered in {elapsed_total:.2f}s", level="info")
        reasoning = getattr(reply, "reasoning", None) or None
        return {
            "reply": reply_str,
            "action": "speak",
            "reasoning": reasoning,
            "memories": [],
            "query": query,
            "debug_info": {
                "backend": backend_name,
                "intent": "chat",
                "reasoning": reasoning,
                "elapsed_s": round(elapsed_total, 2),
                "typesafe_s": round(elapsed_ts, 2),
                "typesafe": typesafe_debug,
                "elapsed_ans_s": round(elapsed_ans, 2),
            },
        }

    # -- QUERY（明确要查备忘/memo/记忆）：拆词 → 一次 CEL → 极速回答 --
    stage = "extract_keywords"
    keywords: list[str] = []
    memos_items: list[dict[str, Any]] = []
    elapsed_kw = elapsed_search = elapsed_ans = 0.0
    try:
        stage = "extract_keywords"
        t_kw = time.monotonic()
        try:
            keywords = await _extract_keywords_with_harness(query, selection=selection)
        except TypeError:
            keywords = await _extract_keywords_with_harness(query)
        elapsed_kw = time.monotonic() - t_kw
        log(f"fast-chat [STAGE: KEYWORDS] in {elapsed_kw:.2f}s: {keywords}", level="info")

        stage = "memos_cel_query"
        t_search = time.monotonic()
        memos_items = await asyncio.to_thread(_search_memos_cel, memos_client, keywords)
        elapsed_search = time.monotonic() - t_search
        log(
            f"fast-chat [STAGE: CEL_SEARCH] in {elapsed_search:.2f}s, found {len(memos_items)} memos",
            level="info",
        )

        stage = "answer"
        t_ans = time.monotonic()
        reply = await _answer_with_direct_llm(query, memos_items, selection=selection)
        if not reply:
            try:
                reply = await _answer_with_harness(query, memos_items, selection=selection)
            except TypeError:
                reply = await _answer_with_harness(query, memos_items)
        elapsed_ans = time.monotonic() - t_ans
        reasoning = getattr(reply, "reasoning", None) or None
        reply_str = str(reply or "") if has_partial_reply() else str(reply or "").strip()
        if not reply_str:
            reply_str = "大王，这个问题我暂时没有好的答案。"
    except Exception as exc:
        if has_partial_reply():
            raise
        import traceback
        tb = traceback.format_exc()
        log(
            f"fast-chat [STAGE: {stage.upper()}_ERROR] 询问链路失败: "
            f"{type(exc).__name__}: {exc}\n{tb}",
            level="error",
        )
        return None

    elapsed_total = time.monotonic() - t0
    log(
        f"fast-chat [STAGE: COMPLETE] query finished in {elapsed_total:.2f}s "
        f"(kw={elapsed_kw:.2f}s, search={elapsed_search:.2f}s, ans={elapsed_ans:.2f}s)",
        level="info",
    )
    memories_out = [
        {
            "id": str(m.get("id", "")),
            "memory": str(m.get("memory", "")),
            "created_at": str(m.get("created_at", "")),
            "score": 1.0,
        }
        for m in memos_items
    ]
    return {
        "reply": reply_str,
        "action": "speak",
        "reasoning": reasoning,
        "memories": memories_out,
        "query": query,
        "debug_info": {
            "backend": "fast-chat",
            "intent": "query",
            "reasoning": reasoning,
            "keywords": keywords,
            "memo_count": len(memos_items),
            "elapsed_s": round(elapsed_total, 2),
            "typesafe_s": round(elapsed_ts, 2),
            "typesafe": typesafe_debug,
            "elapsed_kw_s": round(elapsed_kw, 2),
            "elapsed_search_s": round(elapsed_search, 2),
            "elapsed_ans_s": round(elapsed_ans, 2),
        },
    }
