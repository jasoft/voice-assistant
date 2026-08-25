import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from press_to_talk.reminders import (
    DEFAULT_PROMPT_PATH,
    ReminderCreationError,
    bark_destination,
    build_cron,
    create_reminder_from_text,
    parse_extraction,
    render_prompt,
)


def model_response(payload: dict) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
    }


def test_prompt_is_loaded_from_external_config_and_substitutes_runtime_context():
    now = datetime(2026, 8, 25, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    prompt, config = render_prompt(DEFAULT_PROMPT_PATH, now=now, timezone_name="Asia/Shanghai")

    assert "2026-08-25T16:30:00+08:00" in prompt
    assert "Asia/Shanghai" in prompt
    assert config["schema"]["is_reminder"] is True
    assert "${PTT_CURRENT_TIME}" not in prompt
    assert "每周五的9点提醒我吃药" in prompt


def test_once_reminder_uses_not_before_header_and_bark_path(tmp_path: Path):
    now = datetime(2026, 8, 25, 16, 30, 20, tzinfo=ZoneInfo("Asia/Shanghai"))
    calls = []

    def fake_post(url, method, headers, body):
        calls.append((url, method, headers, body))
        if "chat/completions" in url:
            return 200, model_response({
                "is_reminder": True,
                "message": "起来走走",
                "recurrence": {"type": "once", "by_hour": None},
                "scheduled_at_local": "2026-08-25T16:33:20",
                "confidence": 0.99,
            })
        return 201, {"messageId": "msg_test"}

    result = create_reminder_from_text(
        "3分钟后提醒我起来走走",
        now=now,
        qstash_url="https://qstash.test/",
        qstash_token="token",
        bark_url="https://api.day.app/device-key",
        openai_api_key="openai-key",
        openai_base_url="https://llm.test/v1",
        model="test-model",
        store_path=tmp_path / "reminders.json",
        post_json=fake_post,
    )

    publish_url, method, headers, body = calls[1]
    assert method == "POST"
    assert publish_url.startswith("https://qstash.test/v2/publish/https://api.day.app/device-key/%E8%B5%B7%E6%9D%A5%E8%B5%B0%E8%B5%B0?")
    assert headers["Upstash-Method"] == "GET"
    assert headers["Upstash-Not-Before"] == "1787646800"
    assert body is None
    assert result["kind"] == "once"
    records = json.loads((tmp_path / "reminders.json").read_text())
    assert records[0]["qstash_message_id"] == "msg_test"
    assert records[0]["status"] == "scheduled"


def test_weekly_reminder_creates_utc_cron_schedule(tmp_path: Path):
    now = datetime(2026, 8, 25, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    calls = []

    def fake_post(url, method, headers, body):
        calls.append((url, method, headers, body))
        if "chat/completions" in url:
            return 200, model_response({
                "is_reminder": True,
                "message": "吃药",
                "recurrence": {"type": "weekly", "by_weekday": "friday", "by_hour": 9, "by_minute": 0},
                "scheduled_at_local": None,
                "confidence": 0.99,
            })
        return 201, {"scheduleId": "schedule_test"}

    result = create_reminder_from_text(
        "每周五的9点提醒我吃药",
        now=now,
        qstash_url="https://qstash.test",
        qstash_token="token",
        bark_url="https://api.day.app/key",
        openai_api_key="key",
        openai_base_url="https://llm.test/v1",
        model="model",
        group="Mac提醒",
        store_path=tmp_path / "reminders.json",
        post_json=fake_post,
    )

    endpoint, _, headers, _ = calls[1]
    assert "/v2/schedules/https://api.day.app/key/%E5%90%83%E8%8D%AF?" in endpoint
    assert headers["Upstash-Cron"] == "0 1 * * 5"
    assert headers["Upstash-Method"] == "GET"
    assert result == {
        "kind": "recurring",
        "message": "吃药",
        "remote_id": "schedule_test",
        "display": "每周五 09:00",
        "cron_expression": "0 1 * * 5",
    }


def test_low_confidence_or_non_reminder_is_rejected():
    payload = {"is_reminder": False, "confidence": 0.99}
    with pytest.raises(ReminderCreationError):
        parse_extraction(payload, local_timezone=ZoneInfo("Asia/Shanghai"))

    payload = {"is_reminder": True, "message": "", "confidence": 0.9,
               "recurrence": {"type": "once"}, "scheduled_at_local": "2026-08-26T09:00:00"}
    with pytest.raises(ReminderCreationError, match="提醒内容不能为空"):
        parse_extraction(payload, local_timezone=ZoneInfo("Asia/Shanghai"))


def test_bark_path_escapes_slash_to_keep_one_body_field():
    url = bark_destination("https://api.day.app/key/", "开会/注意", "Mac 提醒")
    assert url == "https://api.day.app/key/%E5%BC%80%E4%BC%9A%2F%E6%B3%A8%E6%84%8F?group=Mac+%E6%8F%90%E9%86%92"


def test_interval_reminder_creates_cron_schedule(tmp_path: Path):
    now = datetime(2026, 8, 25, 16, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    calls = []

    def fake_post(url, method, headers, body):
        calls.append((url, method, headers, body))
        if "chat/completions" in url:
            return 200, model_response({
                "is_reminder": True,
                "message": "站起来走走",
                "recurrence": {"type": "interval", "by_minute": 5, "by_weekday": None, "by_month_day": None, "by_hour": None},
                "scheduled_at_local": None,
                "confidence": 0.99,
            })
        return 201, {"scheduleId": "schedule_interval_test"}

    result = create_reminder_from_text(
        "每隔5分钟提醒我站起来走走",
        now=now,
        qstash_url="https://qstash.test",
        qstash_token="token",
        bark_url="https://api.day.app/key",
        openai_api_key="key",
        openai_base_url="https://llm.test/v1",
        model="model",
        group="Mac提醒",
        store_path=tmp_path / "reminders.json",
        post_json=fake_post,
    )

    endpoint, _, headers, _ = calls[1]
    assert "/v2/schedules/" in endpoint
    assert headers["Upstash-Cron"] == "*/5 * * * *"
    assert headers["Upstash-Method"] == "GET"
    assert result == {
        "kind": "recurring",
        "message": "站起来走走",
        "remote_id": "schedule_interval_test",
        "display": "每隔 5 分钟",
        "cron_expression": "*/5 * * * *",
    }
    records = json.loads((tmp_path / "reminders.json").read_text())
    assert records[0]["is_recurring"] is True
    assert records[0]["cron_expression"] == "*/5 * * * *"
    assert records[0]["schedule_description"] == "每隔 5 分钟"


def test_interval_reminder_missing_by_minute_raises():
    payload = {
        "is_reminder": True,
        "message": "走走",
        "recurrence": {"type": "interval", "by_minute": None, "by_weekday": None, "by_month_day": None, "by_hour": None},
        "scheduled_at_local": None,
        "confidence": 0.99,
    }
    with pytest.raises(ReminderCreationError, match="by_minute"):
        parse_extraction(payload, local_timezone=ZoneInfo("Asia/Shanghai"))


def test_interval_reminder_zero_by_minute_raises():
    payload = {
        "is_reminder": True,
        "message": "走走",
        "recurrence": {"type": "interval", "by_minute": 0, "by_weekday": None, "by_month_day": None, "by_hour": None},
        "scheduled_at_local": None,
        "confidence": 0.99,
    }
    with pytest.raises(ReminderCreationError, match="by_minute"):
        parse_extraction(payload, local_timezone=ZoneInfo("Asia/Shanghai"))
