"""Fast-path handler for record/ask chat.

Pipeline:
  1. TypeSafe once: ask_is_record(query) -> "record" | "other" | None
  2. record -> direct Memos REST API insertion (~0.1-0.3s)
  3. other (询问):
     a. Harness(chat-fast) 拆词 (keywords JSON)
     b. 一次 Memos CEL 查询 (~0.1-0.3s)，异常/空 = 无上下文
     c. Harness(chat-fast) 带上下文回答（无匹配则正常闲聊，可 web_search）

Total target: ≤ 8 s end-to-end for the /v1/chat endpoint.
All prompts are strictly loaded from external workflow_config.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

from ..harness import DeepSeekHarnessClient
from ..storage.providers.memos import (
    MemosClient,
    _strip_tags_and_voice_prefix,
)
from ..utils.env import load_workflow_config
from ..utils.logging import log
from ..utils.typesafe import ask_is_record


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


async def _extract_keywords_with_harness(query: str) -> list[str]:
    """Harness(chat-fast) 拆词：从问句提炼检索关键词。失败返回空列表。"""
    prompt = _prompt("harness_keyword_extract")
    if not prompt:
        prompt = (
            "从下面这句用户问句中提炼 2 到 5 个最核心、最可能命中个人备忘的检索词"
            "（人名、物品、地点、事件等具体实体）。只返回 JSON："
            "{\"keywords\":[\"词1\",\"词2\"]}\n\n用户问句：%s" % query
        )
    else:
        prompt = prompt.replace("%%QUERY%%", query)

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


async def _answer_with_harness(query: str, memos: list[dict[str, Any]]) -> str:
    """Harness(chat-fast) 回答：有匹配备忘则基于备忘回答，无则正常闲聊。"""
    prompt = _prompt("harness_answer")
    memo_lines = [str(m.get("memory", "")).strip() for m in memos if str(m.get("memory", "")).strip()]
    memos_block = "\n".join(memo_lines) or "（无相关备忘）"
    if not prompt:
        prompt = "根据相关备忘回答用户问题；若无相关备忘则直接根据你的知识回答。"
    prompt = prompt.replace("%%QUERY%%", query).replace("%%MEMOS%%", memos_block)

    client = _chat_harness_client()
    try:
        result = await client.query(prompt)
        return str(result.get("reply", "")).strip()
    finally:
        await client.close()


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


_RECORD_STOP_WORDS = [
    "帮我记一下", "帮我记录", "帮我记住", "帮我记下", "帮我存一下", "帮我保存",
    "帮我记个", "帮忙记一下", "帮忙记录", "请记录", "请记住",
    "记一下", "记录一下", "记住", "记下", "存一下", "保存", "记录",
]


def _clean_record_content(text: str) -> str:
    """Strip record command prefixes to get the actual memory content."""
    clean = text.strip()
    for sw in sorted(_RECORD_STOP_WORDS, key=len, reverse=True):
        clean = clean.replace(sw, "")
    clean = re.sub(r"^[：:，。,.\s]+", "", clean).strip()
    return clean or text.strip()


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
) -> dict[str, Any] | None:
    """Attempt a fast-path memory operation.

    Returns a dict with ``reply``, ``memories``, ``query``, ``debug_info``
    on success, or *None* if the fast path cannot serve this query
    (TypeSafe disabled/failed or 询问链路异常——caller falls back to Harness).
    """
    t0 = time.monotonic()
    log(f"fast-chat: 收到查询 query={query[:80]}", level="info")

    # -- Step 1: TypeSafe 一次调用，二分"记录 / 其他（询问）" --
    t_ts = time.monotonic()
    intent = ask_is_record(query)
    elapsed_ts = time.monotonic() - t_ts
    if intent is None:
        log(f"fast-chat: TypeSafe 未启用或调用失败，交由 Harness Agent 回退", level="info")
        return None
    log(f"fast-chat: typesafe intent={intent} in {elapsed_ts:.2f}s", level="info")

    try:
        memos_client = _build_memos_client()
    except Exception as exc:
        log(f"fast-chat: cannot build Memos client: {exc}", level="warn")
        return None

    # -- RECORD：直写 Memos --
    if intent == "record":
        content = _clean_record_content(query)
        try:
            result_text = await asyncio.to_thread(
                _record_memo, memos_client, content, query,
            )
        except Exception as exc:
            err_type = type(exc).__name__
            log(f"fast-chat [STAGE: RECORD] Memos creation failed: {err_type}: {exc}", level="error")
            return {
                "reply": "记录失败，请稍后再试。",
                "memories": [],
                "query": query,
                "debug_info": {
                    "backend": "fast-chat",
                    "intent": "record",
                    "stage": "record_memo",
                    "error_type": err_type,
                    "error_detail": str(exc) or err_type,
                },
            }
        elapsed_total = time.monotonic() - t0
        log(f"fast-chat: record done in {elapsed_total:.2f}s", level="info")
        return {
            "reply": result_text,
            "memories": [],
            "query": query,
            "debug_info": {
                "backend": "fast-chat",
                "intent": "record",
                "elapsed_s": round(elapsed_total, 2),
                "typesafe_s": round(elapsed_ts, 2),
            },
        }

    # -- OTHER（询问）：Harness 拆词 → 一次 CEL → Harness 回答 --
    stage = "extract_keywords"
    keywords: list[str] = []
    memos_items: list[dict[str, Any]] = []
    elapsed_kw = elapsed_search = elapsed_ans = 0.0
    try:
        stage = "extract_keywords"
        t_kw = time.monotonic()
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

        stage = "harness_answer"
        t_ans = time.monotonic()
        reply = await _answer_with_harness(query, memos_items)
        elapsed_ans = time.monotonic() - t_ans
        reply = str(reply or "").strip()
        if not reply:
            reply = "大王，这个问题我暂时没有好的答案。"
    except Exception as exc:
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
        f"fast-chat [STAGE: COMPLETE] ask finished in {elapsed_total:.2f}s "
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
        "reply": reply,
        "memories": memories_out,
        "query": query,
        "debug_info": {
            "backend": "fast-chat",
            "intent": "ask",
            "keywords": keywords,
            "memo_count": len(memos_items),
            "elapsed_s": round(elapsed_total, 2),
            "typesafe_s": round(elapsed_ts, 2),
            "elapsed_kw_s": round(elapsed_kw, 2),
            "elapsed_search_s": round(elapsed_search, 2),
            "elapsed_ans_s": round(elapsed_ans, 2),
        },
    }
