from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import sys
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
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
        yield _FakeResponse({"memos": [], "results": []})

    monkeypatch.setenv("MEMOS_TOKEN", "test-token")
    monkeypatch.setattr("scripts.memo_api.urllib.request.urlopen", fake_urlopen)
    return captured


def test_add_uses_original_text_and_scoped_user(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _main(["add", "--text", "记住钥匙在白柜子"])

    url, payload, headers, method = captured_request[0]
    assert url == "http://ds.home:5230/api/v1/memos"
    assert method == "POST"
    assert payload is not None
    assert payload["content"] == "记住钥匙在白柜子\n\n#voice"
    assert payload["visibility"] == "PRIVATE"
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    assert normalized_headers["authorization"] == "Bearer test-token"
    output = json.loads(capsys.readouterr().out)
    assert output["reply"] == "已记录到 Memos。"


def test_search_filters_by_fixed_user(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
) -> None:
    _main(["search", "--query", "钥匙在哪里", "--limit", "5"])

    url, payload, _, method = captured_request[0]
    assert url.startswith("http://ds.home:5230/api/v1/memos?")
    assert method == "GET"
    assert "filter=" in url


def test_list_pages_within_scope(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
) -> None:
    _main(["list", "--page", "2", "--page-size", "50"])

    url, payload, _headers, method = captured_request[0]
    assert url.startswith("http://ds.home:5230/api/v1/memos?")
    assert method == "GET"
    assert "pageSize=50" in url
    assert payload is None


def test_delete_uses_memory_id_and_delete_method(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _main(["delete", "--id", "memos/memory-123"])

    url, payload, _headers, method = captured_request[0]
    assert url == "http://ds.home:5230/api/v1/memos/memory-123"
    assert payload is None
    assert method == "DELETE"
    output = json.loads(capsys.readouterr().out)
    assert output["reply"] == "已删除。"
    assert output["deleted"] == "memos/memory-123"


def test_search_compacts_mem0_payload(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @contextmanager
    def fake_urlopen(request: Any, timeout: float):
        yield _FakeResponse(
            {
                "memos": [
                    {
                        "name": "memos/memory-1",
                        "content": "钥匙在白柜子\n\n#voice",
                        "createTime": "2026-08-22T00:00:00Z",
                    }
                ]
            }
        )

    monkeypatch.setattr("scripts.memo_api.urllib.request.urlopen", fake_urlopen)

    _main(["search", "--query", "钥匙在哪里", "--limit", "3"])

    assert json.loads(capsys.readouterr().out) == {
        "results": [
            {
                "id": "memos/memory-1",
                "memory": "钥匙在白柜子",
                "created_at": "2026-08-22T00:00:00Z",
                "updated_at": "",
            }
        ],
        "count": 1,
    }


def test_token_falls_back_to_harness_env_file(
    captured_request: list[tuple[str, dict[str, Any] | None, dict[str, str], str]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("MEMOS_TOKEN", raising=False)
    monkeypatch.delenv("MEMOS_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("DSH_HOME", str(tmp_path))
    (tmp_path / ".env").write_text(
        "# machine credentials\n"
        "OTHER_TOKEN=do-not-use\n"
        'MEMOS_TOKEN="file-token"\n',
        encoding="utf-8",
    )

    _main(["list", "--page", "1", "--page-size", "1"])

    headers = captured_request[0][2]
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    assert normalized_headers["authorization"] == "Bearer file-token"


def test_search_recency_query_returns_latest_memos(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @contextmanager
    def fake_urlopen(request: Any, timeout: float):
        yield _FakeResponse(
            {
                "memos": [
                    {
                        "name": "memos/recent-1",
                        "content": "电风扇行情尾声\n\n#voice",
                        "createTime": "2026-09-19T15:00:00Z",
                    },
                    {
                        "name": "memos/recent-2",
                        "content": "AI时代落伍\n\n#voice",
                        "createTime": "2026-09-19T06:00:00Z",
                    },
                ]
            }
        )

    monkeypatch.setattr("scripts.memo_api.urllib.request.urlopen", fake_urlopen)

    _main(["search", "--query", "帮我查一下最近的几篇文章。", "--limit", "2"])

    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 2
    assert out["results"][0]["id"] == "memos/recent-1"
    assert out["results"][0]["memory"] == "电风扇行情尾声"


def test_search_gracefully_handles_network_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @contextmanager
    def fake_urlopen(request: Any, timeout: float):
        raise TimeoutError("timed out")
        yield  # type: ignore

    monkeypatch.setattr("scripts.memo_api.urllib.request.urlopen", fake_urlopen)

    # Should NOT raise SystemExit, should return empty results with warning
    _main(["search", "--query", "帮我查一下关于股指期货的文章。", "--limit", "5"])

    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 0
    assert out["results"] == []
    assert "temporarily unavailable" in out["warning"]

