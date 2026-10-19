import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from weather_api import get_weather
from search_api import search


# --- Weather API Tests ---

def test_weather_empty_location():
    res = get_weather("   ")
    assert "地点不能为空" in res


def test_weather_invalid_days():
    res = get_weather("南京", days=0)
    assert "预报天数必须在 1 到 7 天之间" in res
    res = get_weather("南京", days=8)
    assert "预报天数必须在 1 到 7 天之间" in res


def test_weather_success_mock():
    mock_geo_resp = {
        "results": [
            {
                "name": "南京",
                "admin1": "江苏",
                "country": "中国",
                "latitude": 32.06,
                "longitude": 118.78,
            }
        ]
    }
    mock_forecast_resp = {
        "current": {
            "temperature_2m": 20.5,
            "apparent_temperature": 21.0,
            "relative_humidity_2m": 70,
            "weather_code": 0,
            "wind_speed_10m": 12.0,
        },
        "daily": {
            "time": ["2026-10-19", "2026-10-20"],
            "temperature_2m_max": [22.0, 24.0],
            "temperature_2m_min": [15.0, 16.0],
            "weather_code": [0, 1],
        },
    }

    with patch("weather_api._http_get_json") as mock_get:
        mock_get.side_effect = [mock_geo_resp, mock_forecast_resp]
        res = get_weather(location="南京", days=2)

        assert "【南京，江苏，中国当前天气】" in res
        assert "天气现象: 晴" in res
        assert "实时气温: 20.5℃" in res
        assert "体感温度: 21.0℃" in res
        assert "相对湿度: 70%" in res
        assert "风速: 12.0 km/h" in res
        assert "今天 (2026-10-19): 晴，气温 15.0~22.0℃" in res
        assert "明天 (2026-10-20): 大部晴朗，气温 16.0~24.0℃" in res


def test_weather_not_found():
    with patch("weather_api._http_get_json") as mock_get:
        mock_get.return_value = {"results": []}
        res = get_weather(location="不存在的地名xyz")
        assert "未找到地点" in res


# --- Search API Tests ---

def test_search_empty_query():
    res = search("   ")
    assert res == "搜索词不能为空。"


def test_search_missing_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with patch("search_api._get_api_key", return_value=""):
        res = search("A股新闻")
        assert "未配置 DEEPSEEK_API_KEY" in res


def test_search_success_mock(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "mock-key")

    mock_resp_data = {
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

    mock_urlopen = MagicMock()
    mock_urlopen.__enter__.return_value.read.return_value = json.dumps(mock_resp_data).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=mock_urlopen):
        res = search("今天A股行情", count=2)

        assert "[1] 今天A股大涨 (来源: 证券时报 | 时间: 2026-10-19T15:00:00+08:00)" in res
        assert "链接: https://example.com/news1" in res
        assert "摘要: 上证指数收盘大涨 1.5%，成交量放大。" in res


def test_search_no_results(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "mock-key")
    mock_resp_data = {"results": []}

    mock_urlopen = MagicMock()
    mock_urlopen.__enter__.return_value.read.return_value = json.dumps(mock_resp_data).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=mock_urlopen):
        res = search("某个冷僻词")
        assert "未找到与" in res


def test_search_network_error(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "mock-key")

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection failed")):
        res = search("测试网络失败")
        assert "搜索网络请求失败" in res
