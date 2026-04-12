from __future__ import annotations

import re
import time
from datetime import datetime

def current_time_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

def chat_context_prefix() -> str:
    return f"当前时间：{current_time_text()}。当前位置：南京。"

def format_cn_date(iso_text: str) -> str:
    try:
        # Handle cases where iso_text might be like "2026-04-12T08:21:36-07:00"
        dt = datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
        return f"{dt.year}年{dt.month}月{dt.day}号"
    except Exception:
        return ""

def format_local_datetime(iso_text: str) -> str:
    try:
        dt = datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
        # Convert to local time if needed (currently assuming input is local or handled by fromisoformat)
        weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = weekday_map[dt.weekday()]
        return f"{dt.year}年{dt.month}月{dt.day}号 {weekday} {dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return iso_text or "未知时间"

def normalize_intent_text(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?:：;；“”\"'`~()\[\]{}<>]+", "", text).lower()

def preview_text(text: str, limit: int = 240) -> str:
    clean = text.replace("\n", "\\n").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."

def merge_reply_segments(segments: list[str]) -> str:
    cleaned_segments = [segment.strip() for segment in segments if segment and segment.strip()]
    if not cleaned_segments:
        return ""
    merged = cleaned_segments[0]
    for segment in cleaned_segments[1:]:
        overlap = 0
        max_overlap = min(len(merged), len(segment), 48)
        for size in range(max_overlap, 0, -1):
            if merged.endswith(segment[:size]):
                overlap = size
                break
        merged += segment[overlap:]
    return re.sub(r"\n{3,}", "\n\n", merged).strip()

def strip_think_tags(text: str) -> str:
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*?</think\s*>", "", text)
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*\n", "", cleaned)
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
