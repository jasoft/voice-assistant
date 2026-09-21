"""TypeSafe (Jev System One) client unit tests.

No network: the systemone HTTP call is mocked; response parsing is exercised
directly.
"""

import os

import pytest
from unittest.mock import patch, MagicMock

from press_to_talk.utils import typesafe


@pytest.fixture(autouse=True)
def _typesafe_env():
    """Ensure a key is present so is_configured() is True for parser tests."""
    os.environ["TYPESAFE_API_KEY"] = "test-key"
    yield
    os.environ.pop("TYPESAFE_API_KEY", None)


def _fake_response(payload: dict):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _client_post_mock(mock_client_cls, *, payload=None, side_effect=None):
    """httpx.Client 以上下文管理器使用：post 挂在 __enter__ 返回值上。"""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    if side_effect is not None:
        mock_client.post.side_effect = side_effect
    else:
        mock_client.post.return_value = _fake_response(payload)
    return mock_client


# ---------------------------------------------------------------------------
# Disabled / degraded behaviour
# ---------------------------------------------------------------------------

def test_returns_none_without_key():
    os.environ.pop("TYPESAFE_API_KEY", None)
    assert typesafe.is_configured() is False
    assert typesafe.ask_is_record("我的护照在哪里") is None


def test_returns_none_on_http_failure():
    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, side_effect=RuntimeError("network down"))
        result = typesafe.ask_is_record("我的护照在哪里")
    assert result is None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def test_returns_record():
    payload = {
        "model": "jev-latest",
        "answers": {
            "intent": {"type": "choice", "choice": "record", "confidence": 0.99},
        },
    }
    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, payload=payload)
        result = typesafe.ask_is_record("帮我记一下护照在书房")
    assert result == "record"


def test_returns_other():
    payload = {
        "model": "jev-latest",
        "answers": {
            "intent": {"type": "choice", "choice": "other", "confidence": 0.99},
        },
    }
    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, payload=payload)
        result = typesafe.ask_is_record("你好呀")
    assert result == "other"


def test_returns_none_on_unexpected_choice():
    """旧三态返回 find 时视为无法二分，返回 None 交由上层回退。"""
    payload = {
        "model": "jev-latest",
        "answers": {
            "intent": {"type": "choice", "choice": "find", "confidence": 0.99},
        },
    }
    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, payload=payload)
        result = typesafe.ask_is_record("我的护照在哪里")
    assert result is None


def test_sends_only_intent_question():
    """一次调用只带一个 intent Choice，不带任何关键词 Noul 问题。"""
    payload = {
        "model": "jev-latest",
        "answers": {"intent": {"type": "choice", "choice": "other", "confidence": 0.5}},
    }
    with patch("httpx.Client") as mock_client_cls:
        mock_client = _client_post_mock(mock_client_cls, payload=payload)
        typesafe.ask_is_record("你好呀")
        call = mock_client.post.call_args
        body = call.kwargs.get("json") or call.args[1]
    assert set(body["questions"].keys()) == {"intent"}
    assert body["questions"]["intent"]["type"] == "choice"
    assert "kw_" not in body["questions"]
