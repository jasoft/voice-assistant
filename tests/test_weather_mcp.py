import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from weather_mcp import get_weather


@pytest.mark.parametrize(
    ("location", "days", "message"),
    [
        (" ", 1, "地点不能为空"),
        ("南京", 8, "预报天数必须在 1 到 7 天之间"),
    ],
)
def test_get_weather_rejects_invalid_arguments(location, days, message):
    with pytest.raises(ValueError, match=message):
        asyncio.run(get_weather(location=location, days=days))
