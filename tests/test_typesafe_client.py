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
    old_ts_key = os.environ.get("TYPESAFE_API_KEY")
    old_cf_token = os.environ.get("CLOUDFLARE_AUTH_TOKEN")
    old_cf_acc = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    os.environ["TYPESAFE_API_KEY"] = "test-key"
    os.environ.pop("CLOUDFLARE_AUTH_TOKEN", None)
    os.environ.pop("CLOUDFLARE_ACCOUNT_ID", None)
    yield
    for k, v in [
        ("TYPESAFE_API_KEY", old_ts_key),
        ("CLOUDFLARE_AUTH_TOKEN", old_cf_token),
        ("CLOUDFLARE_ACCOUNT_ID", old_cf_acc),
    ]:
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


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
    os.environ.pop("CLOUDFLARE_AUTH_TOKEN", None)
    os.environ.pop("CLOUDFLARE_ACCOUNT_ID", None)
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


def test_returns_chat():
    payload = {
        "model": "jev-latest",
        "answers": {
            "intent": {"type": "choice", "choice": "chat", "confidence": 0.99},
        },
    }
    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, payload=payload)
        result = typesafe.ask_is_record("你好呀")
    assert result == "chat"


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


def test_sends_intent_and_delivery_questions():
    """配置了 delivery_question 时，一次调用并行带 intent + delivery 两个 Choice。"""
    payload = {
        "model": "jev-latest",
        "answers": {"intent": {"type": "choice", "choice": "chat", "confidence": 0.5}},
    }
    with patch("httpx.Client") as mock_client_cls:
        mock_client = _client_post_mock(mock_client_cls, payload=payload)
        typesafe.ask_is_record("你好呀")
        call = mock_client.post.call_args
        body = call.kwargs.get("json") or call.args[1]
    assert set(body["questions"].keys()) == {"intent", "delivery"}
    assert body["questions"]["intent"]["type"] == "choice"
    assert body["questions"]["delivery"]["type"] == "choice"
    assert "kw_" not in body["questions"]


# ---------------------------------------------------------------------------
# ask_intent_and_delivery: state shape / delivery parsing
# ---------------------------------------------------------------------------

def test_intent_and_delivery_state_is_plain_query_without_selection():
    """无选中文本时 state 保持原始问句字符串，与旧链路一致。"""
    payload = {
        "answers": {"intent": {"type": "choice", "choice": "chat"}},
    }
    with patch("httpx.Client") as mock_client_cls:
        mock_client = _client_post_mock(mock_client_cls, payload=payload)
        typesafe.ask_intent_and_delivery("你好呀")
        body = mock_client.post.call_args.kwargs.get("json")
    assert body["state"] == "你好呀"


def test_intent_and_delivery_state_uses_named_fields_with_selection():
    """带选中文本时 state 用命名字段，指令与选中内容都可见。"""
    payload = {
        "answers": {
            "intent": {"type": "choice", "choice": "chat"},
            "delivery": {"type": "choice", "choice": "paste"},
        },
    }
    with patch("httpx.Client") as mock_client_cls:
        mock_client = _client_post_mock(mock_client_cls, payload=payload)
        result = typesafe.ask_intent_and_delivery(
            "改写成正式一点", "随手写的草稿内容",
        )
        body = mock_client.post.call_args.kwargs.get("json")
    assert body["state"] == {"instruction": "改写成正式一点", "selected_text": "随手写的草稿内容"}
    assert result == {"intent": "chat", "delivery": "paste"}


def test_intent_and_delivery_invalid_delivery_treated_as_none():
    payload = {
        "answers": {
            "intent": {"type": "choice", "choice": "chat"},
            "delivery": {"type": "choice", "choice": "unknown"},
        },
    }
    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, payload=payload)
        result = typesafe.ask_intent_and_delivery("你好呀")
    assert result == {"intent": "chat", "delivery": None}


def test_intent_and_delivery_missing_delivery_answer_treated_as_none():
    payload = {
        "answers": {"intent": {"type": "choice", "choice": "record"}},
    }
    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, payload=payload)
        result = typesafe.ask_intent_and_delivery("帮我记一下")
    assert result == {"intent": "record", "delivery": None}


def test_intent_and_delivery_returns_none_on_bad_intent():
    payload = {
        "answers": {
            "intent": {"type": "choice", "choice": "find"},
            "delivery": {"type": "choice", "choice": "paste"},
        },
    }
    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, payload=payload)
        result = typesafe.ask_intent_and_delivery("我的护照在哪里")
    assert result is None


# ---------------------------------------------------------------------------
# Cloudflare clef-flash mode
# ---------------------------------------------------------------------------

def test_cloudflare_clef_flash_request_and_response_parsing():
    """配置 Cloudflare 凭据时，端点、Header 和 Model 正确指向 clef-flash，且兼容 Workers AI envelope 响应。"""
    os.environ.pop("TYPESAFE_API_KEY", None)
    os.environ["CLOUDFLARE_AUTH_TOKEN"] = "test-token"
    os.environ["CLOUDFLARE_ACCOUNT_ID"] = "test-acc-123"

    assert typesafe.is_configured() is True

    cf_envelope = {
        "result": {
            "model": "clef-flash",
            "answers": {
                "intent": {
                    "type": "choice",
                    "choice": "record",
                    "confidence": 0.98,
                    "probabilities": {"record": 0.98, "chat": 0.02},
                },
                "delivery": {
                    "type": "choice",
                    "choice": "paste",
                    "confidence": 0.95,
                    "probabilities": {"paste": 0.95, "speak": 0.05},
                },
            },
            "usage": {"input_tokens": 100, "output_tokens": 0},
        },
        "success": True,
        "errors": [],
        "messages": [],
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = _client_post_mock(mock_client_cls, payload=cf_envelope)
        result = typesafe.ask_intent_and_delivery("帮我记一下明天下午开会", "会议纪要草稿")
        call = mock_client.post.call_args

    assert result == {"intent": "record", "delivery": "paste"}
    # 验证 URL
    url = call.args[0] if call.args else call.kwargs.get("url")
    assert "https://api.cloudflare.com/client/v4/accounts/test-acc-123/ai/run/@cf/cloudflare/clef-flash" in url
    # 验证 headers
    headers = call.kwargs.get("headers", {})
    assert headers.get("Authorization") == "Bearer test-token"
    # 验证 payload 中的 model
    body = call.kwargs.get("json", {})
    assert body.get("model") == "clef-flash"
    assert body.get("state") == {"instruction": "帮我记一下明天下午开会", "selected_text": "会议纪要草稿"}


def test_cloudflare_clef_flash_return_details():
    """当 return_details=True 时，返回结果包含 details 字典，含模型名、概率分布和置信度。"""
    os.environ.pop("TYPESAFE_API_KEY", None)
    os.environ["CLOUDFLARE_AUTH_TOKEN"] = "test-token"
    os.environ["CLOUDFLARE_ACCOUNT_ID"] = "test-acc-123"

    cf_envelope = {
        "result": {
            "model": "clef-flash",
            "answers": {
                "intent": {
                    "type": "choice",
                    "choice": "record",
                    "confidence": 0.98,
                    "probabilities": {"record": 0.98, "chat": 0.02},
                },
                "delivery": {
                    "type": "choice",
                    "choice": "paste",
                    "confidence": 0.95,
                    "probabilities": {"paste": 0.95, "speak": 0.05},
                },
            },
            "usage": {"input_tokens": 100, "output_tokens": 0},
        },
        "success": True,
        "errors": [],
        "messages": [],
    }

    with patch("httpx.Client") as mock_client_cls:
        _client_post_mock(mock_client_cls, payload=cf_envelope)
        result = typesafe.ask_intent_and_delivery(
            "帮我记一下明天下午开会", "会议纪要草稿", return_details=True
        )

    assert result["intent"] == "record"
    assert result["delivery"] == "paste"
    assert "details" in result
    details = result["details"]
    assert details["model"] == "clef-flash"
    assert details["intent"]["choice"] == "record"
    assert details["intent"]["confidence"] == 0.98
    assert details["intent"]["probabilities"] == {"record": 0.98, "chat": 0.02}
    assert details["delivery"]["choice"] == "paste"
    assert details["delivery"]["confidence"] == 0.95
    assert details["delivery"]["probabilities"] == {"paste": 0.95, "speak": 0.05}


