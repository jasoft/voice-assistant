"""Fast-path handler for memory record/find queries.

Bypasses the DeepSeek Harness Agent entirely:
  1. Regex-based intent detection (~0 ms)
  2. Direct Mem0 API call (~1-3 s)
  3. Single streaming LLM summarization (~2-5 s)

Target: ≤ 8 s end-to-end for the /v1/chat endpoint.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from ..storage.providers.mem0 import (
    Mem0RememberStore,
    create_mem0_client,
    extract_mem0_summary_payload,
)
from ..utils.llm_streaming import build_async_openai_client, stream_chat_completion_text
from ..utils.logging import log
from ..utils.text import current_time_text, format_local_datetime, strip_think_tags


# ---------------------------------------------------------------------------
# Intent detection — pure regex, no LLM needed
# ---------------------------------------------------------------------------

_RECORD_PATTERNS = re.compile(
    r"(?:帮我|帮忙|请)?(?:记一下|记录一下|记录下|记住|记下|存一下|保存|记个|记上|存下)"
    r"|(?:^(?:帮我|帮忙|请)?记录(?!的))"
    r"|(?:^记[：:])"
    r"|(?:放在|放到|搬到).{0,10}(?:了|去了|那了|这了|里了)",
    re.IGNORECASE,
)

_FIND_PATTERNS = re.compile(
    r"(?:帮我|帮忙|请)?(?:查一下|查查|查找|查询|找找|找一下|看看|看一下|搜一下|搜索)"
    r"|(?:在哪|放哪|哪里|哪儿|什么时候|几号|几点|有没有|有多少|是谁|是什么|多少钱)"
    r"|(?:(?:最近|最新|上次|之前|昨天|前天|这周|上周|这个月|上个月)的?.{0,6}(?:记录|备忘|信息|事|东西|笔记))"
    r"|(?:关于.{1,10}的(?:记录|信息|事|备忘))",
    re.IGNORECASE,
)


def classify_memory_intent(text: str) -> str | None:
    """Return 'record', 'find', or None (not a memory query → fall through)."""
    clean = text.strip()
    if not clean:
        return None
    # Check find first — "最近的备忘" should be find, not record
    if _FIND_PATTERNS.search(clean):
        return "find"
    if _RECORD_PATTERNS.search(clean):
        return "record"
    # Default heuristic: short Chinese text that looks like a question → find
    if len(clean) <= 60 and clean.endswith(("？", "?", "吗", "呢", "吧")):
        return "find"
    return None


# ---------------------------------------------------------------------------
# Mem0 helpers
# ---------------------------------------------------------------------------

def _build_mem0_store() -> Mem0RememberStore:
    api_key = os.environ.get("MEM0_API_KEY", "").strip()
    base_url = os.environ.get("MEM0_BASE_URL", "").strip()
    user_id = os.environ.get("MEM0_USER_ID", "soj").strip()
    app_id = os.environ.get("MEM0_APP_ID", "voice-assistant").strip()
    if not api_key:
        raise RuntimeError("MEM0_API_KEY is required for fast-path memory operations")
    return Mem0RememberStore(
        api_key=api_key,
        base_url=base_url,
        user_id=user_id,
        app_id=app_id,
    )


_FIND_STOP_WORDS = [
    "帮我查一下", "帮我查查", "帮我查找", "帮我查询", "帮我查", "帮我找找", "帮我找",
    "帮我看看", "帮我看一下", "查一下", "查查", "查询", "查找", "看看", "看一下",
    "帮我搜一下", "帮我搜索", "搜一下", "搜索",
    "关于", "有关", "涉及", "有没有", "最新的", "最近的", "最新", "最近",
    "几篇", "几条", "几个", "文章", "记录", "备忘", "笔记", "动态", "说说", "内容", "信息",
    "的记录", "的信息", "的事", "的备忘",
]

_RECORD_STOP_WORDS = [
    "帮我记一下", "帮我记录", "帮我记住", "帮我记下", "帮我存一下", "帮我保存",
    "帮我记个", "帮忙记一下", "帮忙记录", "请记录", "请记住",
    "记一下", "记录一下", "记住", "记下", "存一下", "保存",
]


def _clean_find_query(text: str) -> str:
    """Strip command-like prefixes to extract the search kernel."""
    clean = text.strip()
    for sw in sorted(_FIND_STOP_WORDS, key=len, reverse=True):
        clean = clean.replace(sw, "")
    clean = re.sub(r"[，。！？,.!?\s\"']+", " ", clean).strip("的 ").strip()
    return clean or text.strip()


def _clean_record_content(text: str) -> str:
    """Strip record command prefixes to get the actual memory content."""
    clean = text.strip()
    for sw in sorted(_RECORD_STOP_WORDS, key=len, reverse=True):
        clean = clean.replace(sw, "")
    clean = re.sub(r"^[，。,.\s]+", "", clean).strip()
    return clean or text.strip()


# ---------------------------------------------------------------------------
# LLM summarization
# ---------------------------------------------------------------------------

def _build_llm_client() -> Any:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for fast-path summarization")
    return build_async_openai_client(api_key=api_key, base_url=base_url, timeout_seconds=8)


def _llm_model() -> str:
    return os.environ.get("PTT_MODEL", "").strip() or "gpt-4o-mini"


def _build_summary_prompt(user_question: str, memories: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build the system+user messages for summarization."""
    now = current_time_text()
    nickname = os.environ.get("PTT_USER_NICKNAME", "大王")

    system_prompt = (
        f"你是一个智能助手。今天是 {now}。"
        f"人称准则：直接对用户说话，请务必用'{nickname}'称呼用户；"
        "在回答中涉及用户的行为时，严禁使用\u201c用户\u201d一词，必须一律改用\u201c你\u201d或\u201c您\u201d。\n\n"
        "【回答规范】\n"
        "基于检索提供的答案友好回答问题。禁止捏造事实。"
        "如果结果和问题无关，请直接回答原问题。但不要提及数据是检索来的，你只需要总结数据。\n"
        "必须保留关键细节。如果涉及多条记录，必须分行列出详情。\n"
        f"日期：记忆原文的日期如果是今年的（现在是{now}），如果日期是去年的, "
        "那么说'去年M月D日'，再前面的年份就直接说精确年月日。"
        "但如果是最近一个月内的, 就不用提到日期。\n"
        "回复尽量简洁。"
    )

    memory_lines: list[str] = []
    for m in memories:
        mem_text = str(m.get("memory", "")).strip()
        if not mem_text:
            continue
        ts = str(m.get("updated_at") or m.get("created_at") or "").strip()
        date_prefix = ""
        if ts:
            localized = format_local_datetime(ts)
            date_match = re.match(r"^(\d{4})年?(\d{1,2})月?(\d{1,2})", localized)
            if date_match:
                date_prefix = f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}: "
        memory_lines.append(f"{date_prefix}{mem_text}")

    user_prompt = (
        f"我的问题：{user_question}\n"
        f"命中的记忆原文：\n" + "\n".join(memory_lines)
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def try_fast_memory_chat(
    query: str,
    *,
    stream_callback: Any | None = None,
) -> dict[str, Any] | None:
    """Attempt a fast-path memory operation.

    Returns a dict with ``reply``, ``memories``, ``query``, ``debug_info``
    on success, or *None* if the query does not match a memory intent
    (caller should fall back to the Harness Agent).
    """
    t0 = time.monotonic()
    intent = classify_memory_intent(query)
    if intent is None:
        return None

    log(f"fast-chat: intent={intent} query={query[:80]}", level="info")

    try:
        store = _build_mem0_store()
    except RuntimeError as exc:
        log(f"fast-chat: cannot build Mem0 store: {exc}", level="warn")
        return None

    # -- RECORD --
    if intent == "record":
        content = _clean_record_content(query)
        t_store = time.monotonic()
        try:
            result_text = store.add(memory=content, original_text=query)
        except Exception as exc:
            log(f"fast-chat: Mem0 add failed: {exc}", level="error")
            return {
                "reply": "记录失败，请稍后再试。",
                "memories": [],
                "query": query,
                "debug_info": {"backend": "fast-chat", "intent": "record", "error": str(exc)},
            }
        elapsed_store = time.monotonic() - t_store
        elapsed_total = time.monotonic() - t0
        log(f"fast-chat: record done store={elapsed_store:.2f}s total={elapsed_total:.2f}s", level="info")
        return {
            "reply": result_text,
            "memories": [],
            "query": query,
            "debug_info": {
                "backend": "fast-chat",
                "intent": "record",
                "elapsed_s": round(elapsed_total, 2),
            },
        }

    # -- FIND --
    search_query = _clean_find_query(query)
    log(f"fast-chat: search_query={search_query}", level="info")

    # Step 1: Mem0 search (async-wrapped sync call)
    t_search = time.monotonic()
    try:
        import asyncio
        raw_results = await asyncio.to_thread(
            store.find, query=search_query,
        )
    except Exception as exc:
        log(f"fast-chat: Mem0 search failed: {exc}", level="error")
        return {
            "reply": "查询记忆时出错，请稍后再试。",
            "memories": [],
            "query": query,
            "debug_info": {"backend": "fast-chat", "intent": "find", "error": str(exc)},
        }
    elapsed_search = time.monotonic() - t_search
    log(f"fast-chat: Mem0 search done in {elapsed_search:.2f}s", level="info")

    # Parse results
    summary_payload = extract_mem0_summary_payload(raw_results)
    items = summary_payload.get("items", [])

    if not items:
        elapsed_total = time.monotonic() - t0
        return {
            "reply": "没有找到匹配的记忆信息。",
            "memories": [],
            "query": query,
            "debug_info": {
                "backend": "fast-chat",
                "intent": "find",
                "search_query": search_query,
                "elapsed_s": round(elapsed_total, 2),
            },
        }

    # Step 2: LLM summarization
    t_llm = time.monotonic()
    try:
        client = _build_llm_client()
        messages = _build_summary_prompt(query, items)
        raw_summary = await stream_chat_completion_text(
            client,
            model=_llm_model(),
            messages=messages,
            temperature=0,
            callback=stream_callback,
        )
        reply = strip_think_tags(str(raw_summary or "")).strip() or "处理完成。"
    except Exception as exc:
        log(f"fast-chat: LLM summarization failed: {exc}", level="warn")
        # Graceful degradation: return raw memories as bullet points
        lines = []
        for item in items[:5]:
            mem = str(item.get("memory", "")).strip()
            if mem:
                lines.append(f"• {mem}")
        reply = "\n".join(lines) or "找到了记录，但总结失败。"
    finally:
        try:
            await client.close()
        except Exception:
            pass
    elapsed_llm = time.monotonic() - t_llm
    elapsed_total = time.monotonic() - t0
    log(
        f"fast-chat: find done search={elapsed_search:.2f}s llm={elapsed_llm:.2f}s "
        f"total={elapsed_total:.2f}s items={len(items)}",
        level="info",
    )

    # Build memory items for response
    memory_items = []
    for item in items:
        memory_items.append({
            "id": str(item.get("id", "")),
            "memory": str(item.get("memory", "")),
            "created_at": str(item.get("created_at", "")),
            "score": float(item.get("score") or 0.0),
        })

    return {
        "reply": reply,
        "memories": memory_items,
        "query": query,
        "debug_info": {
            "backend": "fast-chat",
            "intent": "find",
            "search_query": search_query,
            "elapsed_s": round(elapsed_total, 2),
            "elapsed_search_s": round(elapsed_search, 2),
            "elapsed_llm_s": round(elapsed_llm, 2),
            "result_count": len(items),
        },
    }
