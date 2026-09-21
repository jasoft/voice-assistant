"""Memos CEL search tests for fast_chat._search_memos_cel.

The simplified chain does a single CEL query with a short timeout; any failure
or empty result means "no context" (returns []), no fallback pagination.
"""

from press_to_talk.api.fast_chat import _search_memos_cel
from press_to_talk.storage.providers.memos import MemosClient


def _memo(name: str, content: str) -> dict:
    return {"name": name, "content": content, "createTime": "2026-09-20T10:00:00Z"}


def test_cel_query_uses_short_timeout():
    calls: list[float | None] = []

    def spy_list(**kwargs):
        calls.append(kwargs.get("timeout"))
        return {"memos": [], "nextPageToken": ""}

    client = MemosClient()
    client.list_memos = spy_list  # type: ignore[method-assign]
    _search_memos_cel(client, ["护照"])
    assert calls, "expected at least one list_memos call"
    assert all(t is not None and t <= 1.5 for t in calls)


def test_cel_query_builds_filter_from_all_keywords():
    captured: dict = {}

    def spy_list(**kwargs):
        captured.update(kwargs)
        return {"memos": [_memo("memos/1", "护照放在书房白柜子")], "nextPageToken": ""}

    client = MemosClient()
    client.list_memos = spy_list  # type: ignore[method-assign]
    items = _search_memos_cel(client, ["护照", "书房"])
    assert "content.contains('护照')" in captured["filter_expr"]
    assert "content.contains('书房')" in captured["filter_expr"]
    assert any("书房白柜子" in i["memory"] for i in items)


def test_empty_keywords_returns_empty_without_query():
    def unexpected(**kwargs):
        raise AssertionError("不应发起查询")

    client = MemosClient()
    client.list_memos = unexpected  # type: ignore[method-assign]
    assert _search_memos_cel(client, []) == []


def test_cel_failure_returns_empty_as_no_context():
    """单次 CEL 失败 = 无上下文，直接返回 []，不再 fallback 翻页。"""

    def down_list(**kwargs):
        raise RuntimeError("Memos down")

    client = MemosClient()
    client.list_memos = down_list  # type: ignore[method-assign]
    assert _search_memos_cel(client, ["护照"]) == []
