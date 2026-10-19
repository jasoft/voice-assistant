import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from search_mcp import search, health


def test_search_empty_query():
    res = asyncio.run(search("   "))
    assert res == "搜索词不能为空。"


def test_search_missing_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    res = asyncio.run(search("A股新闻"))
    assert "未配置 DEEPSEEK_API_KEY" in res


def test_search_success_mock(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "mock-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {
                "title": "今天A股大涨",
                "url": "https://example.com/news1",
                "site_name": "证券时报",
                "date": "2026-10-19T15:00:00+08:00",
                "summary": "上证指数收盘大涨 1.5%，成交量放大。",
                "snippet": "上证指数收盘大涨...",
            }
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = asyncio.run(search("今天A股行情", count=2))

        assert "[1] 今天A股大涨 (来源: 证券时报 | 时间: 2026-10-19T15:00:00+08:00)" in res
        assert "链接: https://example.com/news1" in res
        assert "摘要: 上证指数收盘大涨 1.5%，成交量放大。" in res

        call_args = mock_post.call_args
        assert call_args[0][0] == "https://api.deepseek.com/v1/tools/search"
        payload = call_args[1]["json"]
        assert payload["query"] == "今天A股行情"
        assert payload["count"] == 2
        assert payload["need_content"] is False


def test_search_no_results(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "mock-key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"results": []}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = asyncio.run(search("某个不存在的冷僻字词xyz123"))
        assert "未找到与" in res


def test_search_timeout(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "mock-key")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("timeout")
        res = asyncio.run(search("测试超时"))
        assert "搜索请求超时" in res


def test_health_check():
    mock_req = MagicMock()
    resp = asyncio.run(health(mock_req))
    assert resp.status_code == 200
