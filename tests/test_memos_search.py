"""Memos search resilience tests for fast_chat._search_memos_cel.

Memos API is fast (<0.2s) but can sporadically hang; the search layer must
degrade to a full paged scan with short timeouts instead of waiting out a
slow CEL query.
"""

from unittest.mock import patch

from press_to_talk.api.fast_chat import _search_memos_cel
from press_to_talk.storage.providers.memos import MemosClient


def _memo(name: str, content: str) -> dict:
    return {"name": name, "content": content, "createTime": "2026-09-20T10:00:00Z"}


class _FakeMemos:
    """Fake list_memos: CEL filter always fails, paged full scan works."""

    def __init__(self, memos: list[dict], page_size: int = 100):
        self.memos = memos
        self.page_size = page_size
        self.cel_calls = 0
        self.full_calls = 0

    def list_memos(self, *, page_size, page_token="", filter_expr="", timeout=None):
        if filter_expr:
            self.cel_calls += 1
            raise RuntimeError("CEL engine down (simulated)")
        self.full_calls += 1
        start = (int(page_token) if page_token else 0) * page_size
        page = self.memos[start : start + page_size]
        next_token = str((start // page_size) + 1) if start + page_size < len(self.memos) else ""
        return {"memos": page, "nextPageToken": next_token}


def _client(fake: _FakeMemos) -> MemosClient:
    client = MemosClient()
    client.list_memos = fake.list_memos  # type: ignore[method-assign]
    return client


def test_cel_query_uses_short_timeout():
    """CEL path must pass the configured short timeout so a hang is bounded."""
    calls: list[float | None] = []

    def spy_list(**kwargs):
        calls.append(kwargs.get("timeout"))
        if kwargs.get("filter_expr"):
            raise RuntimeError("down")
        return {"memos": [], "nextPageToken": ""}

    client = MemosClient()
    client.list_memos = spy_list  # type: ignore[method-assign]
    _search_memos_cel(client, ["护照"])
    assert calls, "expected at least one list_memos call"
    assert all(t is not None and t <= 1.5 for t in calls)


def test_fallback_paginates_and_matches_locally():
    memos = [_memo(f"memos/{i}", f"普通备忘 {i}") for i in range(250)]
    memos[199] = _memo("memos/199", "护照放在书房白柜子")
    fake = _FakeMemos(memos, page_size=100)
    items = _search_memos_cel(_client(fake), ["护照"])

    assert fake.cel_calls == 1          # CEL attempted once, failed
    assert fake.full_calls == 3         # paged through all 250 items
    assert any("书房白柜子" in i["memory"] for i in items)


def test_fallback_stops_at_next_token_empty():
    memos = [_memo(f"memos/{i}", f"备忘 {i}") for i in range(30)]
    fake = _FakeMemos(memos, page_size=100)
    items = _search_memos_cel(_client(fake), ["护照"])

    assert fake.full_calls == 1         # single page covers everything
    assert items == []                  # nothing matched locally
