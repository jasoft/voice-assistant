"""TypeSafe (Jev System One) client unit tests.

No network: the systemone HTTP call is mocked; candidate generation and
response parsing are exercised directly.
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


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

def test_gen_candidates_strips_stopwords():
    cands = typesafe.gen_candidates(
        "我的护照在哪里",
        ["我的", "在哪里", "的", "哪里"],
        max_candidates=14,
    )
    assert cands == ["护照"]


def test_gen_candidates_keeps_short_phrases():
    cands = typesafe.gen_candidates(
        "壮壮上次打球什么时候",
        ["上次", "什么时候"],
        max_candidates=14,
    )
    assert cands == ["壮壮", "打球"]


def test_gen_candidates_splits_long_block():
    cands = typesafe.gen_candidates(
        "护照在书房白柜子第一个抽屉里",
        ["在", "里", "第", "个"],
        max_candidates=14,
    )
    # 长块“书房白柜子第一个抽屉”按“第/个”二次切分为子片段
    assert "护照" in cands
    assert "抽屉" in cands


def test_gen_candidates_respects_max():
    cands = typesafe.gen_candidates(
        "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳",
        [],
        max_candidates=3,
    )
    assert len(cands) <= 3


# ---------------------------------------------------------------------------
# Disabled / degraded behaviour
# ---------------------------------------------------------------------------

def _client_post_mock(mock_client_cls, *, payload=None, side_effect=None):
    """httpx.Client 以上下文管理器使用：post 挂在 __enter__ 返回值上。"""
    mock_client = mock_client_cls.return_value.__enter__.return_value
    if side_effect is not None:
        mock_client.post.side_effect = side_effect
    else:
        mock_client.post.return_value = _fake_response(payload)
    return mock_client


def test_returns_none_without_key():
    os.environ.pop("TYPESAFE_API_KEY", None)
    assert typesafe.is_configured() is False
    assert typesafe.ask_intent_and_keywords("我的护照在哪里") is None


def test_returns_none_on_http_failure():
    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, side_effect=RuntimeError("network down"))
        result = typesafe.ask_intent_and_keywords("我的护照在哪里")
    assert result is None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _fake_response(payload: dict):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def test_parses_intent_and_keywords():
    payload = {
        "model": "jev-1.13.0",
        "answers": {
            "intent": {
                "type": "choice",
                "choice": "find",
                "confidence": 0.98,
                "probabilities": {"find": 1.0, "record": 0.0, "other": 0.0},
            },
            "kw_0": {"type": "noul", "noul": 0.84},
            "kw_1": {"type": "noul", "noul": 0.22},
        },
    }
    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, payload=payload)
        result = typesafe.ask_intent_and_keywords("我的护照在哪里")

    assert result is not None
    assert result["intent"] == "find"
    assert result["intent_confidence"] == 0.98
    # kw_0（护照）>= 0.6 阈值被保留；kw_1 被过滤
    assert result["keywords"] == ["护照"]


def test_drops_low_confidence_keywords():
    payload = {
        "model": "jev-1.13.0",
        "answers": {
            "intent": {"type": "choice", "choice": "other", "confidence": 1.0},
            "kw_0": {"type": "noul", "noul": 0.3},
        },
    }
    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, payload=payload)
        result = typesafe.ask_intent_and_keywords("你好呀")

    assert result is not None
    assert result["intent"] == "other"
    assert result["keywords"] == []
