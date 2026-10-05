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


def current_time_with_weekday_text() -> str:
    import os
    env_time = os.environ.get("PTT_CURRENT_TIME")
    if env_time:
        return env_time
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        now = datetime.now(tz)
    except Exception:
        now = datetime.now().astimezone()
    weekday_map = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekday_map[now.weekday()]
    return f"{now.year}年{now.month}月{now.day}日 {weekday} {now.strftime('%H:%M:%S')}"




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


def parse_query_time_range(
    query: str, now: datetime | None = None
) -> tuple[str, str, list[str]] | None:
    """从自然语言问句中解析时间范围，返回 (start_utc_iso, end_utc_iso, matched_words)。

    支持模式：
    - 相对天：今天/今日、昨天/昨日、前天、大前天
    - 相对周：本周/这周、上周/上一周/上个星期
    - 相对月：本月/这个月、上月/上个月
    - 最近N天：最近3天、近一周等
    - 具体年月日/月日：4月15号、2026年4月15日
    - 具体年月/月份：4月份、2026年4月
    - 相对年：今年、去年
    """
    if not query:
        return None

    try:
        import zoneinfo

        tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        now_dt = datetime.now(tz) if now is None else now.astimezone(tz)
    except Exception:
        now_dt = datetime.now().astimezone() if now is None else now.astimezone()
        tz = now_dt.tzinfo or timezone.utc

    from datetime import timedelta

    today_start = datetime(now_dt.year, now_dt.month, now_dt.day, tzinfo=tz)

    def _fmt(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. 相对天
    if "大前天" in query:
        start = today_start - timedelta(days=3)
        end = today_start - timedelta(days=2)
        return (_fmt(start), _fmt(end), ["大前天"])
    if "前天" in query:
        start = today_start - timedelta(days=2)
        end = today_start - timedelta(days=1)
        return (_fmt(start), _fmt(end), ["前天"])
    if "昨天" in query or "昨日" in query:
        start = today_start - timedelta(days=1)
        end = today_start
        return (_fmt(start), _fmt(end), ["昨天", "昨日"])
    if "今天" in query or "今日" in query:
        start = today_start
        end = today_start + timedelta(days=1)
        return (_fmt(start), _fmt(end), ["今天", "今日"])

    # 2. 相对周
    this_week_start = today_start - timedelta(days=now_dt.weekday())
    if any(k in query for k in ("上周", "上一周", "上个星期", "上个礼拜")):
        start = this_week_start - timedelta(days=7)
        end = this_week_start
        return (_fmt(start), _fmt(end), ["上周", "上一周", "上个星期", "上个礼拜"])
    if any(k in query for k in ("本周", "这周", "这个星期", "这个礼拜", "这一周")):
        start = this_week_start
        end = this_week_start + timedelta(days=7)
        return (_fmt(start), _fmt(end), ["本周", "这周", "这个星期", "这个礼拜", "这一周"])

    # 3. 相对月
    this_month_start = datetime(now_dt.year, now_dt.month, 1, tzinfo=tz)
    if now_dt.month == 12:
        next_month_start = datetime(now_dt.year + 1, 1, 1, tzinfo=tz)
    else:
        next_month_start = datetime(now_dt.year, now_dt.month + 1, 1, tzinfo=tz)

    if now_dt.month == 1:
        last_month_start = datetime(now_dt.year - 1, 12, 1, tzinfo=tz)
    else:
        last_month_start = datetime(now_dt.year, now_dt.month - 1, 1, tzinfo=tz)

    if any(k in query for k in ("上个月", "上月")):
        return (_fmt(last_month_start), _fmt(this_month_start), ["上个月", "上月"])
    if any(k in query for k in ("本月", "这个月", "这月")):
        return (_fmt(this_month_start), _fmt(next_month_start), ["本月", "这个月", "这月"])

    # 4. 最近N天 / 近N天
    cn_num_map = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    m_days = re.search(r"(?:最近|近)\s*([0-9]{1,3}|[一二两三四五六七八九十]+)\s*天", query)
    if m_days:
        raw_val = m_days.group(1)
        val = int(raw_val) if raw_val.isdigit() else cn_num_map.get(raw_val, 1)
        start = now_dt - timedelta(days=val)
        end = now_dt + timedelta(seconds=1)
        return (_fmt(start), _fmt(end), [m_days.group(0)])

    # 5. 具体日期：2026年4月15日 / 4月15号
    m_date = re.search(r"(?:(\d{4})年\s*)?(\d{1,2})月(\d{1,2})[日号]", query)
    if m_date:
        yr = int(m_date.group(1)) if m_date.group(1) else now_dt.year
        mo = int(m_date.group(2))
        da = int(m_date.group(3))
        try:
            start = datetime(yr, mo, da, tzinfo=tz)
            end = start + timedelta(days=1)
            return (_fmt(start), _fmt(end), [m_date.group(0)])
        except ValueError:
            pass

    # 6. 具体月份：2026年4月 / 4月份 / 4月
    m_month = re.search(r"(?:(\d{4})年\s*)?(\d{1,2})月(?:份)?", query)
    if m_month:
        yr = int(m_month.group(1)) if m_month.group(1) else now_dt.year
        mo = int(m_month.group(2))
        if 1 <= mo <= 12:
            start = datetime(yr, mo, 1, tzinfo=tz)
            end = datetime(yr + 1, 1, 1, tzinfo=tz) if mo == 12 else datetime(yr, mo + 1, 1, tzinfo=tz)
            return (_fmt(start), _fmt(end), [m_month.group(0)])

    # 7. 相对年：今年 / 去年
    if "今年" in query:
        start = datetime(now_dt.year, 1, 1, tzinfo=tz)
        end = datetime(now_dt.year + 1, 1, 1, tzinfo=tz)
        return (_fmt(start), _fmt(end), ["今年"])
    if "去年" in query:
        start = datetime(now_dt.year - 1, 1, 1, tzinfo=tz)
        end = datetime(now_dt.year, 1, 1, tzinfo=tz)
        return (_fmt(start), _fmt(end), ["去年"])

    return None
