from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Any

import pytest

from scripts.memo_api import _main


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


@pytest.fixture()
def captured_request(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any], dict[str, str]]]:
    captured: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    @contextmanager
    def fake_urlopen(request: Any, timeout: float):
        body = json.loads(request.data.decode("utf-8"))
        captured.append((request.full_url, body, dict(request.headers)))
        yield _FakeResponse({"results": []})

    monkeypatch.setenv("MEM0_API_KEY", "test-token")
    monkeypatch.setattr("scripts.memo_api.urllib.request.urlopen", fake_urlopen)
    return captured


def test_add_uses_original_text_and_scoped_user(
    captured_request: list[tuple[str, dict[str, Any], dict[str, str]]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _main(["add", "--text", "记住钥匙在白柜子"])

    url, payload, headers = captured_request[0]
    assert url == "https://api.mem0.ai/v1/memories/"
    assert payload["messages"] == [{"role": "user", "content": "记住钥匙在白柜子"}]
    assert payload["user_id"] == "soj"
    assert payload["infer"] is False
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    assert normalized_headers["authorization"] == "Token test-token"
    assert normalized_headers["mem0-user-id"] == hashlib.md5(b"test-token").hexdigest()
    assert json.loads(capsys.readouterr().out)["results"] == []


def test_search_filters_by_fixed_user(
    captured_request: list[tuple[str, dict[str, Any], dict[str, str]]],
) -> None:
    _main(["search", "--query", "钥匙在哪里", "--limit", "5"])

    url, payload, _ = captured_request[0]
    assert url == "https://api.mem0.ai/v2/memories/search/"
    assert payload == {
        "query": "钥匙在哪里",
        "filters": {"AND": [{"user_id": "soj"}]},
        "limit": 5,
        "rerank": True,
    }


def test_list_pages_within_scope(captured_request: list[tuple[str, dict[str, Any], dict[str, str]]]) -> None:
    _main(["list", "--page", "2", "--page-size", "50"])

    url, payload, _headers = captured_request[0]
    assert url.startswith("https://api.mem0.ai/v2/memories/?")
    assert "page=2" in url
    assert "page_size=50" in url
    assert payload == {"filters": {"AND": [{"user_id": "soj"}]}}
