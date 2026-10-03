"""Fast-path handler for record/ask chat.

Pipeline:
  1. TypeSafe once: ask_intent_and_delivery(query, selected_text)
     - intent: "record" | "other"
     - delivery: "paste" | "speak" | None（期望产出是粘贴内容还是播报回答）
  2. record -> direct Memos REST API insertion (~0.1-0.3s)
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
        result = await client.query(prompt)
        return str(result.get("reply", "")).strip()
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
        result = await client.query(prompt)
        reply = str(result.get("reply", "")).strip()
        log(f"fast-chat: harness 产出粘贴内容 {len(reply)} 字", level="info")
        return reply or None
    except Exception as exc:
        log(f"fast-chat: harness 产出内容失败: {type(exc).__name__}: {exc}", level="warn")
        return None
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
    selected_text: str | None = None,
) -> dict[str, Any] | None:
    """Attempt a fast-path memory operation.

    Returns a dict with ``reply``, ``action`` ("speak"|"paste"), ``memories``,
    ``query``, ``debug_info`` on success, or *None* if the fast path cannot
    serve this query (TypeSafe disabled/failed or 询问链路异常——caller falls
    back to Harness).
    """
    t0 = time.monotonic()
    selection = (selected_text or "").strip()
    if len(selection) > _MAX_SELECTION_CHARS:
        log(f"fast-chat: 选中文本过长（{len(selection)} 字符），截断至 {_MAX_SELECTION_CHARS}", level="info")
        selection = selection[:_MAX_SELECTION_CHARS]
    log(f"fast-chat: 收到查询 query={query[:80]} selection_len={len(selection)}", level="info")

    # -- Step 1: TypeSafe 一次调用，二分"记录 / 其他（询问）"+ 期望产出方式 --
    t_ts = time.monotonic()
    decision = ask_intent_and_delivery(query, selection or None)
    elapsed_ts = time.monotonic() - t_ts
    if decision is None:
        log(f"fast-chat: TypeSafe 未启用或调用失败，交由 Harness Agent 回退", level="info")
        return None
    intent = decision["intent"]
    delivery = decision.get("delivery")
    log(f"fast-chat: typesafe intent={intent} delivery={delivery} in {elapsed_ts:.2f}s", level="info")

    try:
        memos_client = _build_memos_client()
    except Exception as exc:
        log(f"fast-chat: cannot build Memos client: {exc}", level="warn")
        return None

    # -- RECORD：直写 Memos --
    if intent == "record":
        content = _clean_record_content(query)
        if selection:
            if not content or content in ("这段话", "这个", "这段内容", "选中文本"):
                content = selection
            else:
                content = f"{content}\n\n【选中文本】\n{selection}"
        try:
            result_text = await asyncio.to_thread(
                _record_memo, memos_client, content, query,
            )
        except Exception as exc:
            err_type = type(exc).__name__
            log(f"fast-chat [STAGE: RECORD] Memos creation failed: {err_type}: {exc}", level="error")
            return {
                "reply": "记录失败，请稍后再试。",
                "action": "speak",
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
            "action": "speak",
            "memories": [],
            "query": query,
            "debug_info": {
                "backend": "fast-chat",
                "intent": "record",
                "elapsed_s": round(elapsed_total, 2),
                "typesafe_s": round(elapsed_ts, 2),
            },
        }

    # -- OTHER + PASTE：Harness 按"指令 + 可选选中文本"产出内容，调用方回贴 --
    if delivery == "paste":
        try:
            stage = "compose"
            t_comp = time.monotonic()
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
                    },
                }
            elapsed_total = time.monotonic() - t0
            log(f"fast-chat: compose done in {elapsed_total:.2f}s", level="info")
            return {
                "reply": reply,
                "action": "paste",
                "memories": [],
                "query": query,
                "debug_info": {
                    "backend": "fast-chat",
                    "intent": "compose",
                    "has_selection": bool(selection),
                    "elapsed_s": round(elapsed_total, 2),
                    "typesafe_s": round(elapsed_ts, 2),
                    "elapsed_compose_s": round(elapsed_comp, 2),
                },
            }
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            log(
                f"fast-chat [STAGE: COMPOSE_ERROR] 产出链路失败: "
                f"{type(exc).__name__}: {exc}\n{tb}",
                level="error",
            )
            return None

    # -- CHAT：用户没有明确要求查询备忘/记忆，直接 Harness 回答，不检索 Memos --
    if intent == "chat":
        stage = "harness_answer"
        t_ans = time.monotonic()
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
        reply = str(reply or "").strip()
        if not reply:
            reply = "大王，这个问题我暂时没有好的答案。"
        elapsed_total = time.monotonic() - t0
        log(f"fast-chat: chat answered in {elapsed_total:.2f}s", level="info")
        return {
            "reply": reply,
            "action": "speak",
            "memories": [],
            "query": query,
            "debug_info": {
                "backend": "fast-chat",
                "intent": "chat",
                "elapsed_s": round(elapsed_total, 2),
                "typesafe_s": round(elapsed_ts, 2),
                "elapsed_ans_s": round(elapsed_ans, 2),
            },
        }

    # -- QUERY（明确要查备忘/记忆）：Harness 拆词 → 一次 CEL → Harness 回答 --
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

        stage = "harness_answer"
        t_ans = time.monotonic()
        try:
            reply = await _answer_with_harness(query, memos_items, selection=selection)
        except TypeError:
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
        "action": "speak",
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
