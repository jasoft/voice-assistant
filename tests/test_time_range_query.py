"""Tests for parse_query_time_range and time-based Memos search."""

from datetime import datetime, timezone
import zoneinfo
import pytest

from press_to_talk.utils.text import parse_query_time_range, format_local_datetime
from press_to_talk.api.fast_chat import _search_memos_cel
from press_to_talk.storage.providers.memos import MemosClient


def test_parse_query_time_range_relative_days():
    tz = zoneinfo.ZoneInfo("Asia/Shanghai")
    base_time = datetime(2026, 10, 5, 21, 50, tzinfo=tz)

    # 昨天
    res = parse_query_time_range("查一下昨天记了什么", now=base_time)
    assert res is not None
    start, end, matched = res
    assert start == "2026-10-03T16:00:00Z"
    assert end == "2026-10-04T16:00:00Z"
    assert "昨天" in matched

    # 今天
    res = parse_query_time_range("今天的手表记录", now=base_time)
    assert res is not None
    start, end, matched = res
    assert start == "2026-10-04T16:00:00Z"
    assert end == "2026-10-05T16:00:00Z"
    assert "今天" in matched

    # 前天
    res = parse_query_time_range("前天记了什么", now=base_time)
    assert res is not None
    start, end, matched = res
    assert start == "2026-10-02T16:00:00Z"
    assert end == "2026-10-03T16:00:00Z"


def test_parse_query_time_range_relative_weeks():
    tz = zoneinfo.ZoneInfo("Asia/Shanghai")
    # 2026-10-05 是周一 (weekday() == 0)
    base_time = datetime(2026, 10, 5, 12, 0, tzinfo=tz)

    res_last_week = parse_query_time_range("上周我记了什么", now=base_time)
    assert res_last_week is not None
    start, end, _ = res_last_week
    # 上周一 2026-09-28 00:00 (UTC 2026-09-27T16:00:00Z)
    assert start == "2026-09-27T16:00:00Z"
    # 本周一 2026-10-05 00:00 (UTC 2026-10-04T16:00:00Z)
    assert end == "2026-10-04T16:00:00Z"


def test_parse_query_time_range_specific_month():
    tz = zoneinfo.ZoneInfo("Asia/Shanghai")
    base_time = datetime(2026, 10, 5, 12, 0, tzinfo=tz)

    res = parse_query_time_range("4月份有哪些备忘", now=base_time)
    assert res is not None
    start, end, _ = res
    # 2026-04-01 00:00 (UTC 2026-03-31T16:00:00Z)
    assert start == "2026-03-31T16:00:00Z"
    # 2026-05-01 00:00 (UTC 2026-04-30T16:00:00Z)
    assert end == "2026-04-30T16:00:00Z"


def test_parse_query_time_range_specific_date():
    tz = zoneinfo.ZoneInfo("Asia/Shanghai")
    base_time = datetime(2026, 10, 5, 12, 0, tzinfo=tz)

    res = parse_query_time_range("4月15号有什么记录", now=base_time)
    assert res is not None
    start, end, _ = res
    assert start == "2026-04-14T16:00:00Z"
    assert end == "2026-04-15T16:00:00Z"


def test_format_local_datetime():
    # 验证 ISO 转本地时间
    formatted = format_local_datetime("2026-04-15T07:47:00Z")
    assert "2026年4月15号" in formatted
    assert "15:47" in formatted


def test_search_memos_cel_pure_time_range():
    captured: dict = {}

    def spy_list(**kwargs):
        captured.update(kwargs)
        return {
            "memos": [
                {
                    "name": "memos/1",
                    "content": "昨天完成手表App",
                    "createTime": "2026-10-04T07:40:00Z",
                }
            ],
            "nextPageToken": "",
        }

    client = MemosClient()
    client.list_memos = spy_list  # type: ignore[method-assign]

    # 用户问“昨天记了什么”，拆词得到 ["昨天"]，time_range 识别成功
    time_range = ("2026-10-03T16:00:00Z", "2026-10-04T16:00:00Z", ["昨天"])
    items = _search_memos_cel(client, ["昨天"], time_range=time_range)

    assert "created_ts >= timestamp('2026-10-03T16:00:00Z')" in captured["filter_expr"]
    assert "created_ts < timestamp('2026-10-04T16:00:00Z')" in captured["filter_expr"]
    # "昨天" 作为时间词被过滤，不应在 content.contains 中
    assert "content.contains('昨天')" not in captured["filter_expr"]
    assert captured["page_size"] == 20
    assert len(items) == 1
    assert items[0]["created_at"] == "2026-10-04T07:40:00Z"


def test_search_memos_cel_combined_time_and_keyword():
    captured: dict = {}

    def spy_list(**kwargs):
        captured.update(kwargs)
        return {"memos": [], "nextPageToken": ""}

    client = MemosClient()
    client.list_memos = spy_list  # type: ignore[method-assign]

    time_range = ("2026-10-03T16:00:00Z", "2026-10-04T16:00:00Z", ["昨天"])
    _search_memos_cel(client, ["昨天", "手表"], time_range=time_range)

    # 应该组合 (created_ts ...) && (content.contains('手表'))
    assert "created_ts >= timestamp" in captured["filter_expr"]
    assert "content.contains('手表')" in captured["filter_expr"]
    assert "content.contains('昨天')" not in captured["filter_expr"]
    assert captured["page_size"] == 10
