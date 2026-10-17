from __future__ import annotations

from typing import Literal

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse


mcp = FastMCP("voice-assistant-weather", host="0.0.0.0", port=3081)

_WEATHER_CODES = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "雾",
    48: "冻雾",
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


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.tool()
async def get_weather(
    location: str = "南京",
    days: int = 1,
    units: Literal["celsius", "fahrenheit"] = "celsius",
) -> str:
    """查询指定地点当前天气；days 大于 1 时附带从今天开始的逐日预报。"""
    location = location.strip()
    if not location:
        raise ValueError("地点不能为空")
    if not 1 <= days <= 7:
        raise ValueError("预报天数必须在 1 到 7 天之间")

    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        geocoding = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 5, "language": "zh", "format": "json"},
        )
        geocoding.raise_for_status()
        matches = geocoding.json().get("results") or []
        if not matches:
            raise ValueError(f"找不到地点：{location}")
        place = matches[0]

        params: dict[str, str | int | float] = {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "timezone": "auto",
            "temperature_unit": units,
            "wind_speed_unit": "kmh",
        }
        if days > 1:
            params.update(
                daily="temperature_2m_max,temperature_2m_min,weather_code",
                forecast_days=days,
            )
        forecast = await client.get(
            "https://api.open-meteo.com/v1/forecast", params=params
        )
        forecast.raise_for_status()
        data = forecast.json()

    current = data.get("current")
    if not current:
        raise RuntimeError(f"天气服务没有返回 {location} 的当前天气")
    suffix = "华氏度" if units == "fahrenheit" else "摄氏度"
    place_name = "，".join(
        part for part in (place.get("name"), place.get("admin1"), place.get("country")) if part
    )
    code = int(current.get("weather_code", -1))
    lines = [
        f"{place_name} 当前天气：{_WEATHER_CODES.get(code, '天气状况未知')}，"
        f"气温 {current.get('temperature_2m')} {suffix}，"
        f"体感 {current.get('apparent_temperature')} {suffix}，"
        f"湿度 {current.get('relative_humidity_2m')}%，"
        f"风速 {current.get('wind_speed_10m')} 公里每小时（当地时间 {current.get('time')}）。"
    ]
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []
    for index, date in enumerate(dates):
        if index >= min(len(highs), len(lows), len(codes)):
            break
        lines.append(
            f"{place_name} {date} 预报：{_WEATHER_CODES.get(codes[index], '天气状况未知')}，"
            f"最低 {lows[index]} {suffix}，最高 {highs[index]} {suffix}。"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
