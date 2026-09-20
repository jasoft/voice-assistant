"""Fast-path handler for memory record/find queries.

Pipeline:
  1. Regex-based intent detection (~0 ms) -> record / find
  2. For find:
     a. LLM entity/keyword extraction (~0.8-1.5 s) based on workflow_config.json prompts
     b. Memos CEL query execution using extracted keywords (`content.contains('词')`) (~0.1-0.3 s)
     c. LLM summarization on matched memos (~1-2 s)
  3. For record:
     Direct Memos REST API insertion (~0.1-0.3 s)

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

from ..storage.providers.memos import (
    MemosClient,
    _strip_tags_and_voice_prefix,
)
from ..utils.env import load_workflow_config, render_prompt_template
from ..utils.llm_streaming import build_async_openai_client, stream_chat_completion_text
from ..utils.logging import log
from ..utils.text import current_time_text, format_local_datetime, strip_think_tags


# ---------------------------------------------------------------------------
# Intent detection — fast regex, no LLM needed
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
    """Return 'record', 'find', or None (not a memory query -> fall through to Harness)."""
    clean = text.strip()
    if not clean:
        return None
    # Check find first — "最近的备忘" should be find, not record
    if _FIND_PATTERNS.search(clean):
        return "find"
    if _RECORD_PATTERNS.search(clean):
        return "record"
    # Default heuristic: short Chinese text that looks like a question -> find
    if len(clean) <= 60 and clean.endswith(("？", "?", "吗", "呢", "吧")):
        return "find"
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


_RECORD_STOP_WORDS = [
    "帮我记一下", "帮我记录", "帮我记住", "帮我记下", "帮我存一下", "帮我保存",
    "帮我记个", "帮忙记一下", "帮忙记录", "请记录", "请记住",
    "记一下", "记录一下", "记住", "记下", "存一下", "保存",
]


def _clean_record_content(text: str) -> str:
    """Strip record command prefixes to get the actual memory content."""
    clean = text.strip()
    for sw in sorted(_RECORD_STOP_WORDS, key=len, reverse=True):
        clean = clean.replace(sw, "")
    clean = re.sub(r"^[，。,.\s]+", "", clean).strip()
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


def _search_memos_cel(client: MemosClient, keywords: list[str], *, page_size: int = 10) -> list[dict[str, Any]]:
    """Execute CEL query on Memos using extracted keywords."""
    valid_words = [
        w.strip() for w in keywords
        if w.strip() and not any(c in w for c in ("'", '"', "\\", "\n", "\r"))
    ]
    if not valid_words:
        return []

    # Build CEL filter: content.contains('词1') || content.contains('词2')
    cel_parts = [f"content.contains('{w}')" for w in valid_words]
    filter_expr = " || ".join(cel_parts)

    try:
        log(f"fast-chat: CEL query expr: {filter_expr}", level="info")
        res = client.list_memos(page_size=page_size, filter_expr=filter_expr)
        raw_memos = res.get("memos", [])
    except Exception as exc:
        log(f"fast-chat: CEL query failed: {exc}", level="warn")
        raw_memos = []

    # Fallback to recent memos search if CEL query returns empty
    if not raw_memos:
        try:
            res = client.list_memos(page_size=30)
            candidates = res.get("memos", [])
            for m in candidates:
                content = str(m.get("content", ""))
                if any(w in content for w in valid_words):
                    raw_memos.append(m)
        except Exception as exc:
            log(f"fast-chat: fallback list search failed: {exc}", level="warn")

    items: list[dict[str, Any]] = []
    for memo in raw_memos:
        content = str(memo.get("content", "")).strip()
        if not content:
            continue
        clean_memory = _strip_tags_and_voice_prefix(content)
        if not clean_memory:
            continue
        created = str(memo.get("createTime", "")).strip()
        updated = str(memo.get("updateTime", "") or created).strip()
        items.append({
            "id": str(memo.get("name", "")),
            "memory": clean_memory,
            "raw_content": content,
            "created_at": created,
            "updated_at": updated,
        })
    return items


# ---------------------------------------------------------------------------
# LLM helpers (Prompts loaded strictly from workflow_config.json)
# ---------------------------------------------------------------------------

def _build_llm_client() -> Any:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for fast-path summarization")
    return build_async_openai_client(api_key=api_key, base_url=base_url, timeout_seconds=8)


def _llm_model() -> str:
    return os.environ.get("PTT_MODEL", "").strip() or "fast"


def _parse_keywords_json(text: str) -> list[str]:
    """Parse JSON array or object containing keywords from LLM output."""
    raw = strip_think_tags(text).strip()
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
    # Fallback if bare list
    match_arr = re.search(r"\[.*\]", raw, re.DOTALL)
    if match_arr:
        try:
            data = json.loads(match_arr.group(0))
            if isinstance(data, list):
                return [str(k).strip() for k in data if str(k).strip()]
        except Exception:
            pass
    return []


async def _extract_keywords_with_llm(client: Any, query: str) -> list[str]:
    """Extract search entity keywords using external query_rewrite prompt."""
    cfg = load_workflow_config()
    prompts = cfg.get("prompts", {})
    rewrite_cfg = prompts.get("query_rewrite", {})
    system_prompt = rewrite_cfg.get(
        "system_prompt",
        "你是一个检索词提炼器。请从用户原问句中提炼 1 到 4 个最核心的实体词或短语。不要问句尾巴，只返回 JSON：{\"keywords\":[\"词1\"]}",
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"问句：{query}"},
    ]

    try:
        raw = await stream_chat_completion_text(
            client,
            model=_llm_model(),
            messages=messages,
            temperature=0,
        )
        keywords = _parse_keywords_json(raw)
        if keywords:
            return keywords
    except Exception as exc:
        log(f"fast-chat: LLM keyword extraction failed: {exc}", level="warn")

    # Fallback to simple regex token if LLM extraction fails
    fallback_words = re.findall(r"[\u4e00-\u9fa5a-zA-Z0-9]{2,}", query)
    noise = {"我的", "你的", "是在", "在哪里", "在哪", "什么时候", "怎么", "一下", "帮我"}
    return [w for w in fallback_words if w not in noise][:3]


def _build_summary_messages(
    user_question: str,
    memos: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build summary prompt from workflow_config.json remember_summary template."""
    cfg = load_workflow_config()
    prompts = cfg.get("prompts", {})
    summary_cfg = prompts.get("remember_summary", {})

    now = current_time_text()
    nickname = os.environ.get("PTT_USER_NICKNAME", "大王")
    template = summary_cfg.get("system_prompt", "")
    system_prompt = render_prompt_template(
        template,
        {
            "PTT_CURRENT_TIME": now,
            "USER_NICKNAME": nickname,
        },
    )

    memo_lines: list[str] = []
    for m in memos:
        ts = str(m.get("updated_at") or m.get("created_at") or "").strip()
        date_str = ""
        if ts:
            localized = format_local_datetime(ts)
            date_match = re.match(r"^(\d{4})年?(\d{1,2})月?(\d{1,2})", localized)
            if date_match:
                date_str = f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
        mem_text = str(m.get("memory", "")).strip()
        memo_lines.append(f"[{date_str}] {mem_text}" if date_str else mem_text)

    memos_block = "\n".join(memo_lines)
    user_prompt = (
        f"用户问题：{user_question}\n"
        f"相关备忘录：\n{memos_block}"
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
        memos_client = _build_memos_client()
    except Exception as exc:
        log(f"fast-chat: cannot build Memos client: {exc}", level="warn")
        return None

    # -- RECORD --
    if intent == "record":
        content = _clean_record_content(query)
        try:
            result_text = await asyncio.to_thread(
                _record_memo, memos_client, content, query,
            )
        except Exception as exc:
            log(f"fast-chat: Memos add failed: {exc}", level="error")
            return {
                "reply": "记录失败，请稍后再试。",
                "memories": [],
                "query": query,
                "debug_info": {"backend": "fast-chat", "intent": "record", "error": str(exc)},
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
            },
        }

    # -- FIND --
    client = None
    try:
        client = _build_llm_client()

        # Step 1: LLM tokenize/extract keywords (~0.8-1.5s)
        t_kw = time.monotonic()
        keywords = await _extract_keywords_with_llm(client, query)
        elapsed_kw = time.monotonic() - t_kw
        log(f"fast-chat: LLM extracted keywords in {elapsed_kw:.2f}s: {keywords}", level="info")

        # Step 2: Memos CEL query (~0.1-0.3s)
        t_search = time.monotonic()
        memos_items = await asyncio.to_thread(
            _search_memos_cel, memos_client, keywords, page_size=10,
        )
        elapsed_search = time.monotonic() - t_search
        log(f"fast-chat: CEL search in {elapsed_search:.2f}s, found {len(memos_items)} memos", level="info")

        if not memos_items:
            elapsed_total = time.monotonic() - t0
            return {
                "reply": f"大王，没有找到与“{'、'.join(keywords) if keywords else query}”相关的备忘记录。",
                "memories": [],
                "query": query,
                "debug_info": {
                    "backend": "fast-chat",
                    "intent": "find",
                    "keywords": keywords,
                    "elapsed_s": round(elapsed_total, 2),
                    "elapsed_kw_s": round(elapsed_kw, 2),
                    "elapsed_search_s": round(elapsed_search, 2),
                },
            }

        # Step 3: LLM summarization (~1-2s)
        t_sum = time.monotonic()
        messages = _build_summary_messages(query, memos_items)
        raw_reply = await stream_chat_completion_text(
            client,
            model=_llm_model(),
            messages=messages,
            temperature=0,
            callback=stream_callback,
        )
        reply = strip_think_tags(str(raw_reply or "")).strip() or "处理完成。"
        elapsed_sum = time.monotonic() - t_sum
        elapsed_total = time.monotonic() - t0
        log(
            f"fast-chat: find complete total={elapsed_total:.2f}s "
            f"(kw={elapsed_kw:.2f}s, search={elapsed_search:.2f}s, sum={elapsed_sum:.2f}s)",
            level="info",
        )

        # Build memories payload for API response
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
                "intent": "find",
                "keywords": keywords,
                "memo_count": len(memos_items),
                "elapsed_s": round(elapsed_total, 2),
                "elapsed_kw_s": round(elapsed_kw, 2),
                "elapsed_search_s": round(elapsed_search, 2),
                "elapsed_sum_s": round(elapsed_sum, 2),
            },
        }

    except Exception as exc:
        log(f"fast-chat: find error: {exc}", level="error")
        return {
            "reply": "查询记忆时出错，请稍后再试。",
            "memories": [],
            "query": query,
            "debug_info": {"backend": "fast-chat", "intent": "find", "error": str(exc)},
        }
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
