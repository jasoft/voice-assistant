from __future__ import annotations

import re
import json
from typing import Any

def wants_explicit_search(text: str) -> bool:
    normalized = re.sub(r"[ \t]+", "", text or "")
    return "联网搜索" in normalized or "上网搜索" in normalized

def is_list_request(text: str) -> bool:
    normalized = re.sub(r"[ \t]+", "", text or "")
    list_markers = [
        "列出来",
        "列一下",
        "列出",
        "全部记录",
        "所有记录",
        "都有哪些",
        "有哪些",
        "清单",
        "列表",
    ]
    return any(marker in normalized for marker in list_markers)

def derive_find_query(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    patterns = [
        r"^(?:帮我)?(?:联网搜索|上网搜索|搜索|查找|查询|查一下|找一下|帮我查一下|帮我找一下)(?:关于)?",
        r"^(?:我想)?(?:知道|看看|了解)(?:一下)?(?:关于)?",
        r"(?:的信息|的事情|的情况|有哪些|是什么|是什么时候|什么时候|在哪里|在哪|多少)$",
    ]
    query = normalized
    for pattern in patterns:
        query = re.sub(pattern, "", query)
    query = re.sub(r"[，。！？、,.!?]+", "", query).strip()
    return query or normalized

def coerce_to_local_find_payload(
    user_input: str, payload: dict[str, Any] | None = None, *, note: str = ""
) -> dict[str, Any]:
    original = payload if isinstance(payload, dict) else {}
    original_args = original.get("args", {})
    args = original_args if isinstance(original_args, dict) else {}
    query = str(args.get("query", "") or "").strip()
    tool_name = "remember_list" if is_list_request(user_input) and not query else "remember_find"
    if tool_name == "remember_list":
        query = ""
    else:
        query = query or derive_find_query(user_input)
    return {
        "intent": "find",
        "tool": tool_name,
        "args": {
            "memory": "",
            "query": query,
            "note": "",
        },
        "confidence": max(float(original.get("confidence", 0.0) or 0.0), 0.6),
        "notes": note or "默认归入本地查询",
    }

def prefers_local_record(text: str) -> bool:
    normalized = re.sub(r"[ \t]+", "", text or "")
    if wants_explicit_search(normalized):
        return False
    record_markers = [
        "记住",
        "帮我记一下",
        "帮我记住",
        "记一下",
        "记录",
        "保存",
        "更新",
    ]
    return any(marker in normalized for marker in record_markers)

def prefers_local_find(text: str) -> bool:
    normalized = re.sub(r"[ \t]+", "", text or "")
    if wants_explicit_search(normalized):
        return False
    local_markers = [
        "找",
        "查找",
        "查询",
        "在哪",
        "位置",
        "哪里",
        "怎么记",
        "记住",
        "什么时间",
        "什么时候",
        "生日",
        "特征",
        "属性",
    ]
    return any(marker in normalized for marker in local_markers)

def detect_local_intent(text: str) -> str | None:
    if prefers_local_record(text):
        return "record"
    if prefers_local_find(text):
        return "find"
    return None

def salvage_truncated_intent_payload(text: str) -> dict[str, Any] | None:
    intent_match = re.search(r'"intent"\s*:\s*"([^"]+)"', text)
    if not intent_match:
        return None

    tool_match = re.search(r'"tool"\s*:\s*(null|"([^"]+)")', text)
    confidence_match = re.search(r'"confidence"\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
    notes_match = re.search(r'"notes"\s*:\s*"([^"]*)', text)
    args_section = re.search(r'"args"\s*:\s*\{(.*)', text, flags=re.S)

    args: dict[str, str] = {
        "item": "",
        "content": "",
        "type": "",
        "query": "",
        "image": "",
        "note": "",
    }
    if args_section:
        for key in args:
            match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]*)', args_section.group(1))
            if match:
                args[key] = match.group(1)

    tool_value: str | None = None
    if tool_match:
        tool_value = tool_match.group(2) if tool_match.group(1) != "null" else None

    return {
        "intent": intent_match.group(1),
        "tool": tool_value,
        "args": args,
        "confidence": float(confidence_match.group(1)) if confidence_match else 0.0,
        "notes": notes_match.group(1) if notes_match else "",
    }
