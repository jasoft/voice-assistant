from __future__ import annotations

import uuid
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT_PATH = PROJECT_ROOT / "config" / "reminders" / "natural_language.json"
DEFAULT_STORE_PATH = PROJECT_ROOT / ".mac_gui_reminders.json"
WEEKDAYS = {"monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6, "sunday": 7}
JsonPoster = Callable[[str, str, dict[str, str], str | None], tuple[int, dict[str, Any] | str]]


def configured_store_path() -> Path:
    """Return the durable reminder store used by API and CLI callers."""
    return Path(os.getenv("PTT_REMINDER_STORE_PATH", str(DEFAULT_STORE_PATH)))


def load_reminder_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return [item for item in loaded if isinstance(item, dict)] if isinstance(loaded, list) else []


def save_reminder_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def cancel_remote_reminder(
    remote_id: str,
    *,
    qstash_url: str,
    qstash_token: str,
    is_recurring: bool,
) -> None:
    endpoint = f"{qstash_url.rstrip('/')}/{'v2/schedules' if is_recurring else 'v2/messages'}/{remote_id}"
    status, _ = default_post_json(endpoint, "DELETE", {"Authorization": f"Bearer {qstash_token}"}, None)
    if status not in {200, 201, 202, 204}:
        raise ReminderCreationError(f"QStash 取消失败：HTTP {status}")


class ReminderCreationError(RuntimeError):
    """Raised when natural language cannot safely become a QStash reminder."""


@dataclass(frozen=True)
class Extraction:
    message: str
    recurrence_type: str
    weekday: int | None
    month_day: int | None
    hour: int | None
    minute: int | None
    scheduled_at_local: datetime | None
    confidence: float


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def render_prompt(path: Path = DEFAULT_PROMPT_PATH, *, now: datetime, timezone_name: str) -> tuple[str, dict[str, Any]]:
    config = _read_json(path)
    replacements = {
        "${PTT_CURRENT_TIME}": now.isoformat(timespec="seconds"),
        "${REMINDER_TIMEZONE}": timezone_name,
        "${EXAMPLE_ONCE_LOCAL}": (now + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S"),
    }
    prompt = config["system_prompt"]
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)

    # Examples are documentation for the model; keep the sample time current.
    examples = json.dumps(config.get("examples", []), ensure_ascii=False)
    for key, value in replacements.items():
        examples = examples.replace(key, value)
    rendered = prompt + "\n\nJSON schema:\n" + json.dumps(config["schema"], ensure_ascii=False)
    rendered += "\n\nExamples:\n" + examples
    rendered += "\n\nRules:\n" + "\n".join(f"- {item}" for item in config["instructions"])
    return rendered, config


def _extract_json(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReminderCreationError("提醒解析模型没有返回有效 JSON") from exc
    if not isinstance(decoded, dict):
        raise ReminderCreationError("提醒解析模型返回类型不正确")
    return decoded


def parse_extraction(payload: dict[str, Any], *, local_timezone: ZoneInfo) -> Extraction:
    if payload.get("is_reminder") is not True:
        raise ReminderCreationError("这不是一个提醒请求")
    message = str(payload.get("message") or "").strip()
    if not message:
        raise ReminderCreationError("提醒内容不能为空")
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError) as exc:
        raise ReminderCreationError("提醒解析置信度无效") from exc

    recurrence = payload.get("recurrence")
    if not isinstance(recurrence, dict):
        raise ReminderCreationError("提醒重复规则缺失")
    recurrence_type = str(recurrence.get("type") or "").lower()
    if recurrence_type not in {"once", "daily", "weekly", "monthly"}:
        raise ReminderCreationError("提醒重复规则无效")

    raw_weekday = recurrence.get("by_weekday")
    weekday = WEEKDAYS.get(str(raw_weekday).lower()) if raw_weekday else None
    month_day = int(recurrence["by_month_day"]) if recurrence.get("by_month_day") is not None else None
    hour = int(recurrence["by_hour"]) if recurrence.get("by_hour") is not None else None
    minute = int(recurrence.get("by_minute") or 0)

    scheduled_local: datetime | None = None
    if recurrence_type == "once":
        raw_time = str(payload.get("scheduled_at_local") or "")
        try:
            scheduled_local = datetime.fromisoformat(raw_time)
        except ValueError as exc:
            raise ReminderCreationError("一次性提醒时间无效") from exc
        if scheduled_local.tzinfo is not None:
            scheduled_local = scheduled_local.astimezone(local_timezone).replace(tzinfo=None)
        hour, minute = scheduled_local.hour, scheduled_local.minute
    elif recurrence_type == "weekly" and weekday is None:
        raise ReminderCreationError("每周提醒缺少星期")
    elif recurrence_type == "monthly" and not 1 <= (month_day or 0) <= 31:
        raise ReminderCreationError("每月提醒缺少有效日期")
    if hour is None or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ReminderCreationError("提醒小时或分钟无效")

    return Extraction(message, recurrence_type, weekday, month_day, hour, minute, scheduled_local, confidence)


def build_cron(extraction: Extraction, *, source_timezone: ZoneInfo, at_utc: datetime) -> tuple[str, datetime, str]:
    local_now = at_utc.astimezone(source_timezone)
    if extraction.recurrence_type == "once":
        local_time = extraction.scheduled_at_local or local_now.replace(tzinfo=None)
        return "", local_time.replace(tzinfo=source_timezone), ""

    # QStash evaluates cron in UTC. Construct the selected local wall-clock time,
    # then normalize it so a Friday 09:00 Asia/Shanghai becomes Friday 01:00 UTC.
    if extraction.recurrence_type == "daily":
        candidate_local = local_now.replace(hour=extraction.hour, minute=extraction.minute, second=0, microsecond=0)
    elif extraction.recurrence_type == "weekly":
        days_ahead = ((extraction.weekday or local_now.isoweekday()) - local_now.isoweekday()) % 7
        candidate_local = (local_now + timedelta(days=days_ahead)).replace(
            hour=extraction.hour, minute=extraction.minute, second=0, microsecond=0
        )
    else:
        candidate_local = local_now.replace(
            day=extraction.month_day or local_now.day,
            hour=extraction.hour, minute=extraction.minute, second=0, microsecond=0
        )
    candidate_utc = candidate_local.astimezone(timezone.utc)
    if extraction.recurrence_type == "daily":
        return f"{candidate_utc.minute} {candidate_utc.hour} * * *", local_now, f"每天 {extraction.hour:02d}:{extraction.minute:02d}"
    if extraction.recurrence_type == "weekly":
        names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        local_weekday = extraction.weekday or candidate_local.isoweekday()
        cron = f"{candidate_utc.minute} {candidate_utc.hour} * * {candidate_utc.isoweekday() % 7}"
        return cron, local_now, f"每{names[local_weekday - 1]} {extraction.hour:02d}:{extraction.minute:02d}"
    return (
        f"{candidate_utc.minute} {candidate_utc.hour} {candidate_utc.day} * *",
        local_now,
        f"每月 {extraction.month_day} 日 {extraction.hour:02d}:{extraction.minute:02d}",
    )


def bark_destination(base_url: str, message: str, group: str, sound: str | None = None) -> str:
    encoded = urllib.parse.quote(message, safe="")
    query = [("group", group)]
    if sound:
        query.append(("sound", sound))
    return f"{base_url.rstrip('/')}/{encoded}?{urllib.parse.urlencode(query)}"


def default_post_json(url: str, method: str, headers: dict[str, str], body: str | None) -> tuple[int, dict[str, Any] | str]:
    request = urllib.request.Request(url, method=method, data=body.encode("utf-8") if body else None)
    for key, value in headers.items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read().decode("utf-8", "replace")
    try:
        return response.status, json.loads(payload)
    except json.JSONDecodeError:
        return response.status, payload


def create_reminder_from_text(
    text: str,
    *,
    now: datetime,
    qstash_url: str,
    qstash_token: str,
    bark_url: str,
    openai_api_key: str,
    openai_base_url: str,
    model: str,
    timezone_name: str = "Asia/Shanghai",
    group: str = "Mac提醒",
    sound: str | None = None,
    prompt_path: Path = DEFAULT_PROMPT_PATH,
    store_path: Path = DEFAULT_STORE_PATH,
    post_json: JsonPoster = default_post_json,
) -> dict[str, Any]:
    if not text.strip():
        raise ReminderCreationError("提醒原文不能为空")
    source_zone = ZoneInfo(timezone_name)
    utc_now = now.astimezone(timezone.utc)
    system_prompt, _ = render_prompt(prompt_path, now=now, timezone_name=timezone_name)
    request_body = json.dumps(
        {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text.strip()},
            ],
        },
        ensure_ascii=False,
    )
    status, response = post_json(
        f"{openai_base_url.rstrip('/')}/chat/completions",
        "POST",
        {"Authorization": f"Bearer {openai_api_key}", "Content-Type": "application/json"},
        request_body,
    )
    if status != 200 or not isinstance(response, dict):
        raise ReminderCreationError("提醒解析模型请求失败")
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ReminderCreationError("提醒解析模型响应缺少内容") from exc
    payload = _extract_json(str(content))
    extraction = parse_extraction(payload, local_timezone=source_zone)
    if extraction.confidence < 0.80:
        notes = str(payload.get("notes") or "时间不够明确").strip()
        raise ReminderCreationError(f"时间不够明确：{notes}")

    destination = bark_destination(bark_url, extraction.message, group, sound)
    cron, next_occurrence_utc, description = build_cron(extraction, source_timezone=source_zone, at_utc=utc_now)
    if extraction.recurrence_type == "once":
        epoch = int(next_occurrence_utc.timestamp())
        endpoint = f"{qstash_url.rstrip('/')}/v2/publish/{destination}"
        headers = {
            "Authorization": f"Bearer {qstash_token}",
            "Upstash-Not-Before": str(epoch),
            "Upstash-Method": "GET",
        }
        display = next_occurrence_utc.strftime("%m-%d %H:%M")
    else:
        endpoint = f"{qstash_url.rstrip('/')}/v2/schedules/{destination}"
        headers = {
            "Authorization": f"Bearer {qstash_token}",
            "Upstash-Cron": cron,
            "Upstash-Method": "GET",
        }
        display = description

    status, result = post_json(endpoint, "POST", headers, None)
    if status not in {200, 201} or not isinstance(result, dict):
        raise ReminderCreationError(f"QStash 创建失败：HTTP {status}")
    remote_id = str(
        result.get("messageId")
        or result.get("message_id")
        or result.get("scheduleId")
        or result.get("schedule_id")
        or ""
    )
    if not remote_id:
        raise ReminderCreationError("QStash 没有返回远程 ID")

    record = {
        "id": str(uuid.uuid4()).upper(),
        "qstash_message_id": remote_id,
        "message": extraction.message,
        "scheduled_at": next_occurrence_utc.isoformat(timespec="seconds"),
        "created_at": utc_now.isoformat(timespec="seconds"),
        "status": "scheduled",
        "is_recurring": extraction.recurrence_type != "once",
        "cron_expression": cron or None,
        "timezone_identifier": timezone_name if extraction.recurrence_type != "once" else None,
        "schedule_description": display if extraction.recurrence_type != "once" else None,
    }
    records = load_reminder_records(store_path)
    records.append(record)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    save_reminder_records(store_path, records)
    return {
        "kind": "recurring" if record["is_recurring"] else "once",
        "message": extraction.message,
        "remote_id": remote_id,
        "display": display,
        "cron_expression": cron or None,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    from .utils.env import load_env_files

    parser = argparse.ArgumentParser(description="Create a QStash Bark reminder from natural language.")
    parser.add_argument("command", choices=["create"])
    parser.add_argument("--text", required=True)
    args = parser.parse_args(argv)
    load_env_files()

    missing = [
        name for name in ("QSTASH_TOKEN", "BARK_URL", "OPENAI_API_KEY", "PTT_MODEL")
        if not os.getenv(name)
    ]
    if missing:
        print(json.dumps({"error": "missing_config", "keys": missing}, ensure_ascii=False))
        return 2
    try:
        result = create_reminder_from_text(
            args.text,
            now=datetime.now(ZoneInfo(os.getenv("REMINDER_TIMEZONE", "Asia/Shanghai"))),
            qstash_url=os.getenv("QSTASH_URL", "https://qstash.upstash.io"),
            qstash_token=os.environ["QSTASH_TOKEN"],
            bark_url=os.environ["BARK_URL"],
            openai_api_key=os.environ["OPENAI_API_KEY"],
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.environ["PTT_MODEL"],
            timezone_name=os.getenv("REMINDER_TIMEZONE", "Asia/Shanghai"),
            group=os.getenv("REMINDER_GROUP", "Mac提醒"),
            sound=os.getenv("REMINDER_SOUND"),
        )
    except (ReminderCreationError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0
