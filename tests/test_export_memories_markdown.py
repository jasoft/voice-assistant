from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from export_memories_markdown import fetch_all_records, render_markdown


def test_render_groups_users_and_preserves_dates_and_content() -> None:
    records = [
        {
            "id": "new",
            "user_id": "alice",
            "memory": "新记忆",
            "original_text": "帮我记住新记忆",
            "created": "2026-08-02 12:00:00.000Z",
            "updated": "2026-08-02 12:01:00.000Z",
            "photo_path": "photos/a.jpg",
            "embedding_json": "[0.1]",
        },
        {
            "id": "old",
            "user_id": "alice",
            "memory": "旧记忆",
            "original_text": "旧原话",
            "created": "2026-04-14 03:00:00.000Z",
            "updated": "2026-04-14 03:00:00.000Z",
            "photo_path": "",
            "embedding_json": "",
        },
        {
            "id": "bob-record",
            "user_id": "bob|with-pipe",
            "memory": "另一条记忆",
            "original_text": "原话",
            "created": "2026-05-01 00:00:00.000Z",
            "updated": "2026-05-01 00:00:00.000Z",
        },
    ]

    output = render_markdown(
        records,
        base_url="http://docker.home:18090",
        exported_at="2026-08-19T12:00:00+08:00",
    )

    assert "总记录数：3；用户数：2" in output
    assert "## 用户：`alice`" in output
    assert "## 用户：`bob|with-pipe`" in output
    assert "| bob\\|with-pipe | 1 |" in output
    assert output.index("新记忆") < output.index("旧记忆")
    assert "创建时间（源数据）：`2026-08-02 12:00:00.000Z`" in output
    assert "原始表达：帮我记住新记忆" in output
    assert "照片路径：`photos/a.jpg`" in output
    assert "向量索引：`present`" in output
    assert "向量索引：`absent`" in output
    assert "embedding_json" in output


def test_fetch_all_records_rejects_incomplete_response() -> None:
    payload = json.dumps(
        {"page": 1, "totalPages": 1, "totalItems": 2, "items": [{"id": "only"} ]}
    ).encode()

    def opener(*args, **kwargs):
        return BytesIO(payload)

    with pytest.raises(RuntimeError, match="incomplete memory export"):
        fetch_all_records("http://example.test", opener=opener)


def test_fetch_all_records_rejects_duplicate_ids() -> None:
    payload = json.dumps(
        {"page": 1, "totalPages": 1, "totalItems": 2, "items": [{"id": "same"}, {"id": "same"}]}
    ).encode()

    def opener(*args, **kwargs):
        return BytesIO(payload)

    with pytest.raises(RuntimeError, match="duplicate record IDs"):
        fetch_all_records("http://example.test", opener=opener)
