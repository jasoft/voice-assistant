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
def captured_request(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, Any] | None, dict[str, str], str]]:
    captured: list[tuple[str, dict[str, Any] | None, dict[str, str], str]] = []

    @contextmanager
    def fake_urlopen(request: Any, timeout: float):
        body = json.loads(request.data.decode("utf-8")) if request.data is not None else None
        captured.append((request.full_url, body, dict(request.headers), request.method))
        yield _FakeResponse({"results": []})

    monkeypatch.setenv("MEM0_API_KEY", "test-token")
    monkeypatch.setattr("scripts.memo_api.urllib.request.urlopen", fake_urlopen)
    return captured


def test_add_uses_original_text_and_scoped_user(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _main(["add", "--text", "记住钥匙在白柜子"])

    url, payload, headers, method = captured_request[0]
    assert url == "https://api.mem0.ai/v1/memories/"
    assert method == "POST"
    assert payload is not None
    assert payload["messages"] == [{"role": "user", "content": "记住钥匙在白柜子"}]
    assert payload["user_id"] == "soj"
    assert payload["infer"] is False
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    assert normalized_headers["authorization"] == "Token test-token"
    assert normalized_headers["mem0-user-id"] == hashlib.md5(b"test-token").hexdigest()
    output = json.loads(capsys.readouterr().out)
    assert output["reply"] == "已记录。"
    assert output["results"] == []


def test_search_filters_by_fixed_user(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
) -> None:
    _main(["search", "--query", "钥匙在哪里", "--limit", "5"])

    url, payload, _, method = captured_request[0]
    assert url == "https://api.mem0.ai/v2/memories/search/"
    assert method == "POST"
    assert payload == {
        "query": "钥匙在哪里",
        "filters": {"AND": [{"user_id": "soj"}]},
        "top_k": 5,
        "rerank": False,
    }


def test_list_pages_within_scope(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
) -> None:
    _main(["list", "--page", "2", "--page-size", "50"])

    url, payload, _headers, method = captured_request[0]
    assert url.startswith("https://api.mem0.ai/v2/memories/?")
    assert method == "POST"
    assert "page=2" in url
    assert "page_size=50" in url
    assert payload == {"filters": {"AND": [{"user_id": "soj"}]}}


def test_delete_uses_memory_id_and_delete_method(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _main(["delete", "--id", "memory-123"])

    url, payload, _headers, method = captured_request[0]
    assert url == "https://api.mem0.ai/v1/memories/memory-123/"
    assert payload is None
    assert method == "DELETE"
    output = json.loads(capsys.readouterr().out)
    assert output["reply"] == "已删除。"
    assert output["deleted"] == "memory-123"


def test_search_compacts_mem0_payload(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @contextmanager
    def fake_urlopen(request: Any, timeout: float):
        yield _FakeResponse(
            [
                {
                    "id": "memory-1",
                    "memory": "钥匙在白柜子",
                    "score": 0.9,
                    "created_at": "2026-08-22T00:00:00Z",
                    "categories": ["location"],
                    "metadata": {"large": "unused"},
                    "structured_attributes": {"unused": True},
                }
            ]
        )

    monkeypatch.setattr("scripts.memo_api.urllib.request.urlopen", fake_urlopen)

    _main(["search", "--query", "钥匙在哪里", "--limit", "3"])

    assert json.loads(capsys.readouterr().out) == {
        "results": [
            {
                "id": "memory-1",
                "memory": "钥匙在白柜子",
                "score": 0.9,
                "created_at": "2026-08-22T00:00:00Z",
            }
        ]
    }


def test_token_falls_back_to_harness_env_file(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    monkeypatch.delenv("MEM0_MCP_TOKEN", raising=False)
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "# machine credentials\n"
        "OTHER_TOKEN=do-not-use\n"
        'MEM0_API_KEY="file-token"\n',
        encoding="utf-8",
    )

    _main(["list", "--page", "1", "--page-size", "1"])

    headers = captured_request[0][2]
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    assert normalized_headers["authorization"] == "Token file-token"
    assert normalized_headers["mem0-user-id"] == hashlib.md5(b"file-token").hexdigest()
