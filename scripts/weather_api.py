#!/usr/bin/env python3
"""Weather query CLI tool using Open-Meteo API. Zero external dependencies."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_WEATHER_CODES = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "沉积雾",
    51: "毛毛雨",
    53: "中等毛毛雨",
    55: "强毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "阵雨",
    81: "中等阵雨",
    82: "强阵雨",
    85: "阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴大冰雹",
}


def _http_get_json(url: str, params: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
    query_str = urllib.parse.urlencode(params)
    full_url = f"{url}?{query_str}"
    req = urllib.request.Request(
        full_url,
        headers={"User-Agent": "VoiceAssistant-Weather/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_weather(location: str = "南京", days: int = 1, units: str = "celsius") -> str:
    location = location.strip()
    if not location:
        return "查询失败：地点不能为空。"
    if not 1 <= days <= 7:
        return "查询失败：预报天数必须在 1 到 7 天之间。"

    try:
        geocoding_data = _http_get_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            {"name": location, "count": 5, "language": "zh", "format": "json"},
            timeout=8.0,
        )
        matches = geocoding_data.get("results") or []
        if not matches:
            return f"未找到地点：“{location}”的天气信息。"

        place = matches[0]
        params: dict[str, Any] = {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "timezone": "auto",
            "temperature_unit": units,
            "wind_speed_unit": "kmh",
        }
        if days > 1:
            params["daily"] = "temperature_2m_max,temperature_2m_min,weather_code"
            params["forecast_days"] = days

        forecast_data = _http_get_json(
            "https://api.open-meteo.com/v1/forecast",
            params,
            timeout=8.0,
        )
    except urllib.error.URLError as exc:
        return f"天气网络请求失败: {exc.reason if hasattr(exc, 'reason') else exc}"
    except Exception as exc:
        return f"天气查询出错: {exc}"

    current = forecast_data.get("current")
    if not current:
        return f"天气服务未返回 {location} 的当前数据。"

    suffix = "°F" if units == "fahrenheit" else "℃"
    place_parts = [p for p in (place.get("name"), place.get("admin1"), place.get("country")) if p]
    place_name = "，".join(place_parts)

    weather_desc = _WEATHER_CODES.get(current.get("weather_code"), "未知")
    lines = [
        f"【{place_name}当前天气】",
        f"天气现象: {weather_desc}",
        f"实时气温: {current.get('temperature_2m')}{suffix}",
        f"体感温度: {current.get('apparent_temperature')}{suffix}",
        f"相对湿度: {current.get('relative_humidity_2m')}%",
        f"风速: {current.get('wind_speed_10m')} km/h",
    ]

    daily = forecast_data.get("daily")
    if daily and days > 1:
        dates = daily.get("time", [])
        t_max = daily.get("temperature_2m_max", [])
        t_min = daily.get("temperature_2m_min", [])
        w_codes = daily.get("weather_code", [])
        lines.append("\n【逐日预报】")
        for i in range(min(days, len(dates))):
            d_date = dates[i]
            d_desc = _WEATHER_CODES.get(w_codes[i] if i < len(w_codes) else None, "未知")
            max_v = t_max[i] if i < len(t_max) else "--"
            min_v = t_min[i] if i < len(t_min) else "--"
            prefix = "今天" if i == 0 else ("明天" if i == 1 else f"{d_date}")
            lines.append(f"{prefix} ({d_date}): {d_desc}，气温 {min_v}~{max_v}{suffix}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Query weather for a given location.")
    parser.add_argument("--location", "-l", default="南京", help="城市或地点名称（默认南京）")
    parser.add_argument("--days", "-d", type=int, default=1, help="预报天数 1-7（默认 1）")
    parser.add_argument(
        "--units", "-u", choices=["celsius", "fahrenheit"], default="celsius", help="温度单位"
    )

    args = parser.parse_args()
    result = get_weather(location=args.location, days=args.days, units=args.units)
    print(result)


if __name__ == "__main__":
    main()
