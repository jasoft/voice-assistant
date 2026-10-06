"""TypeSafe (Jev System One) client: millisecond record/other decision.

A single ``POST /v1/systemone`` call evaluates independent choice questions:
  - is this utterance a "record" (保存信息) or "other" (询问/闲聊)?
  - when a ``selected_text`` is provided: should the output be "paste"
    (改写/生成一段要插入光标处的内容) or "speak" (常规语音播报回答)?

All question wording lives in external ``workflow_config.json`` (AGENTS.md rule:
no hard-coded prompts). Every failure degrades gracefully: callers fall back
to the Harness Agent.
"""

from __future__ import annotations

import os
from typing import Any

from ..utils.env import load_workflow_config
from ..utils.logging import log

_DEFAULT_API_URL = "https://api.typesafe.ai/v1/systemone"
_DEFAULT_MODEL = "jev-latest"
_DEFAULT_CF_MODEL = "clef-flash"


def is_configured() -> bool:
    """决策模型在配置了 Cloudflare Workers AI 或 TypeSafe API key 时启用。"""
    cf_token = (os.environ.get("CLOUDFLARE_AUTH_TOKEN") or "").strip()
    cf_account = (os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    if cf_token and cf_account:
        return True
    return bool((os.environ.get("TYPESAFE_API_KEY") or "").strip())


def _cfg() -> dict[str, Any]:
    cfg = load_workflow_config().get("typesafe", {})
    return cfg if isinstance(cfg, dict) else {}


def _get_request_config(cfg: dict[str, Any]) -> tuple[str, str, dict[str, str]] | None:
    """返回 (api_url, model, headers)，若未配置有效凭证则返回 None。"""
    cf_token = (os.environ.get("CLOUDFLARE_AUTH_TOKEN") or "").strip()
    cf_account = (os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    ts_key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()

    if cf_token and cf_account:
        raw_url = str(os.environ.get("CLOUDFLARE_API_URL") or cfg.get("api_url") or "").strip()
        if not raw_url or "typesafe.ai" in raw_url or "${CLOUDFLARE_ACCOUNT_ID}" in raw_url or "//ai/run" in raw_url:
            api_url = f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/run/@cf/cloudflare/clef-flash"
        elif "{account_id}" in raw_url:
            api_url = raw_url.format(account_id=cf_account)
        else:
            api_url = raw_url

        configured_model = str(cfg.get("model") or "").strip()
        model = (
            configured_model
            if configured_model and configured_model != "jev-latest"
            else _DEFAULT_CF_MODEL
        )
        headers = {
            "Authorization": f"Bearer {cf_token}",
            "Content-Type": "application/json",
        }
        return api_url, model, headers

    if ts_key:
        api_url = str(os.environ.get("TYPESAFE_API_URL") or cfg.get("api_url") or _DEFAULT_API_URL)
        model = str(cfg.get("model") or _DEFAULT_MODEL)
        headers = {
            "Authorization": f"Bearer {ts_key}",
            "Content-Type": "application/json",
        }
        return api_url, model, headers

    return None


def _post_systemone(
    payload: dict[str, Any],
    *,
    api_url: str,
    timeout: float,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                api_url,
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        log(
            f"typesafe: decision call failed: {type(exc).__name__}: {exc}",
            level="warn",
        )
        return None


def _choice_answer(answers: Any, question: str, valid: tuple[str, ...]) -> str | None:
    if not isinstance(answers, dict):
        return None
    answer = answers.get(question)
    if isinstance(answer, dict) and answer.get("type") == "choice":
        choice = answer.get("choice")
        if choice in valid:
            return str(choice)
    return None


def ask_intent_and_delivery(
    query: str,
    selected_text: str | None = None,
    *,
    return_details: bool = False,
) -> dict[str, Any] | None:
    """一次 Clef / TypeSafe 调用并行判断两个独立问题：

    - ``intent``：这句话是"记录"还是"其他（询问）"；
    - ``delivery``：期望产出是"paste"（改写/生成一段要插入光标处的内容）
      还是"speak"（常规语音播报回答）。仅当配置了 ``delivery_question`` 时询问。

    有选中文本时 state 用命名字段（instruction + selected_text），让模型能同时
    参考指令与选中内容。Returns ``{"intent": ..., "delivery": ...}`` or ``None``
    when disabled / failed / intent 无法二分 (caller falls back to Harness).
    当 ``return_details=True`` 时，返回字典中额外包含 ``details``（模型名、逐项概率、置信度等）。
    """
    if not is_configured():
        return None
    cfg = _cfg()
    req_config = _get_request_config(cfg)
    if req_config is None:
        return None
    api_url, model, headers = req_config
    timeout = float(cfg.get("timeout_seconds", 3.0))

    intent_q = cfg.get("intent_question")
    if not isinstance(intent_q, dict):
        return None

    selection = (selected_text or "").strip()
    state: Any = {"instruction": query, "selected_text": selection} if selection else query
    questions: dict[str, Any] = {
        "intent": {
            "type": "choice",
            "instructions": str(intent_q.get("instructions", "")),
            "criteria": {
                str(k): str(v)
                for k, v in (intent_q.get("criteria") or {}).items()
            },
        }
    }
    delivery_q = cfg.get("delivery_question")
    if isinstance(delivery_q, dict):
        questions["delivery"] = {
            "type": "choice",
            "instructions": str(delivery_q.get("instructions", "")),
            "criteria": {
                str(k): str(v)
                for k, v in (delivery_q.get("criteria") or {}).items()
            },
        }
    payload = {
        "state": state,
        "model": model,
        "questions": questions,
    }

    data = _post_systemone(payload, api_url=api_url, timeout=timeout, headers=headers)
    if data is None:
        return None

    result = data.get("result")
    if isinstance(result, dict) and "answers" in result:
        answers = result.get("answers")
    else:
        answers = data.get("answers")

    intent = _choice_answer(answers, "intent", ("record", "query", "chat", "agent", "task"))
    if intent is None:
        log("typesafe: intent 无法二分，交由上层回退", level="info")
        return None
    delivery = _choice_answer(answers, "delivery", ("paste", "speak"))
    log(f"typesafe: intent={intent} delivery={delivery}", level="info")

    if not return_details:
        return {"intent": intent, "delivery": delivery}

    intent_obj = answers.get("intent") if isinstance(answers, dict) else {}
    delivery_obj = answers.get("delivery") if isinstance(answers, dict) else {}

    details = {
        "model": model,
        "intent": {
            "choice": intent,
            "confidence": intent_obj.get("confidence") if isinstance(intent_obj, dict) else None,
            "probabilities": intent_obj.get("probabilities") if isinstance(intent_obj, dict) else {},
        },
        "delivery": {
            "choice": delivery,
            "confidence": delivery_obj.get("confidence") if isinstance(delivery_obj, dict) else None,
            "probabilities": delivery_obj.get("probabilities") if isinstance(delivery_obj, dict) else {},
        },
        "raw_answers": answers,
    }
    return {"intent": intent, "delivery": delivery, "details": details}


def ask_is_record(query: str) -> str | None:
    """一次 TypeSafe Choice 调用：判断用户的话是“记录”还是“其他（询问）”。

    Returns ``"record"``, ``"other"``, or ``None`` when disabled / failed
    (caller falls back to the Harness Agent).
    """
    decision = ask_intent_and_delivery(query)
    return decision["intent"] if decision else None
