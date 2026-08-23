from __future__ import annotations

import re
import time
from datetime import datetime, timezone

def current_time_text() -> str:
    import os
    env_time = os.environ.get("PTT_CURRENT_TIME")
    if env_time:
        return env_time
    return time.strftime("%Y-%m-%d %H:%M:%S")



def format_local_datetime(iso_text: str) -> str:
    try:
        dt = datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone()
        weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = weekday_map[dt.weekday()]
        return f"{dt.year}年{dt.month}月{dt.day}号 {weekday} {dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return iso_text or "未知时间"




def strip_think_tags(text: str) -> str:
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*?</think\s*>", "", text)
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*\n", "", cleaned)
    cleaned = re.sub(r"(?is)<think\b[^>]*>.*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
