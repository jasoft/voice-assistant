from __future__ import annotations

import re
from typing import Any

def prefers_local_record(text: str) -> bool:
    normalized = re.sub(r"[\s,，。！？]+", "", text or "")
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

def detect_local_intent(text: str) -> str:
    if prefers_local_record(text):
        return "record"
    return "find"

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
