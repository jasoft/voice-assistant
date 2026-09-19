import json
from unittest.mock import MagicMock, patch
import pytest

from press_to_talk.storage.models import StorageConfig
from press_to_talk.storage.providers.memos import (
    MemosClient,
    MemosRememberStore,
    _strip_tags_and_voice_prefix,
    extract_memos_summary_payload,
)
from press_to_talk.storage.service import StorageService, load_storage_config


def test_strip_tags_and_voice_prefix():
    content = "护照在书房第一个抽屉里\n\n> 语音原文: 帮我记一下护照在书房第一个抽屉里\n\n#voice #important"
    cleaned = _strip_tags_and_voice_prefix(content)
    assert cleaned == "护照在书房第一个抽屉里"

    plain = "今天下午3点开会"
    assert _strip_tags_and_voice_prefix(plain) == "今天下午3点开会"


def test_memos_client_headers():
    client = MemosClient(base_url="http://ds.home:5230", token="test_token_123")
    headers = client.headers
    assert headers["Authorization"] == "Bearer test_token_123"
    assert headers["Content-Type"] == "application/json"
    assert client.base_url == "http://ds.home:5230"


def test_memos_remember_store_add():
    mock_client = MagicMock(spec=MemosClient)
    mock_client.create_memo.return_value = {"name": "memos/test12345", "content": "test"}

    store = MemosRememberStore(mock_client, user_id="soj", visibility="PRIVATE", tag="voice")
    reply = store.add(memory="明天早上记得买牛奶", original_text="帮我记一下明天早上记得买牛奶")

    assert "✅ 已记入 Memos：明天早上记得买牛奶" in reply
    mock_client.create_memo.assert_called_once()
    call_args = mock_client.create_memo.call_args
    created_content = call_args[0][0]
    assert "明天早上记得买牛奶" in created_content
    assert "> 语音原文: 帮我记一下明天早上记得买牛奶" in created_content
    assert "#voice" in created_content
    assert call_args[1]["visibility"] == "PRIVATE"


def test_memos_remember_store_find_cel_and_fallback():
    mock_client = MagicMock(spec=MemosClient)
    # CEL query returns matching memo
    mock_client.list_memos.return_value = {
        "memos": [
            {
                "name": "memos/memo1",
                "content": "明天早上九点去医院体检\n\n#voice",
                "createTime": "2026-09-19T08:00:00Z",
                "updateTime": "2026-09-19T08:00:00Z",
            }
        ]
    }

    store = MemosRememberStore(mock_client)
    res_str = store.find(query="体检")
    mock_client.list_memos.assert_called_with(page_size=50, filter_expr="content.contains('体检')")

    payload = json.loads(res_str)
    items = payload.get("items", [])
    assert len(items) == 1
    assert items[0]["id"] == "memos/memo1"
    assert items[0]["memory"] == "明天早上九点去医院体检"


def test_memos_remember_store_find_date_range():
    mock_client = MagicMock(spec=MemosClient)
    mock_client.list_memos.return_value = {
        "memos": [
            {
                "name": "memos/memo1",
                "content": "昨天买了一本书\n\n#voice",
                "createTime": "2026-09-18T10:00:00Z",
            },
            {
                "name": "memos/memo2",
                "content": "今天去健身房锻炼\n\n#voice",
                "createTime": "2026-09-19T10:00:00Z",
            },
        ]
    }

    store = MemosRememberStore(mock_client)
    res_str = store.find(query="", start_date="2026-09-19", end_date="2026-09-19")
    payload = json.loads(res_str)
    items = payload.get("items", [])
    assert len(items) == 1
    assert items[0]["id"] == "memos/memo2"


def test_memos_summary_extractor():
    raw = {
        "memos": [
            {
                "name": "memos/abc123",
                "content": "钥匙在玄关柜上\n\n#voice",
                "createTime": "2026-09-19T12:00:00Z",
                "tags": ["voice"],
            }
        ]
    }
    extracted = extract_memos_summary_payload(raw)
    items = extracted.get("items", [])
    assert len(items) == 1
    assert items[0]["id"] == "memos/abc123"
    assert items[0]["memory"] == "钥匙在玄关柜上"


def test_storage_service_memos_integration(monkeypatch):
    monkeypatch.setenv("PTT_REMEMBER_BACKEND", "memos")
    monkeypatch.setenv("MEMOS_BASE_URL", "http://ds.home:5230")
    monkeypatch.setenv("MEMOS_TOKEN", "test_token")

    cfg = load_storage_config()
    assert cfg.backend == "memos"
    assert cfg.memos_base_url == "http://ds.home:5230"
    assert cfg.memos_token == "test_token"

    svc = StorageService(cfg)
    store = svc.remember_store()
    assert isinstance(store, MemosRememberStore)
    assert store.client.base_url == "http://ds.home:5230"
    assert store.client.token == "test_token"
