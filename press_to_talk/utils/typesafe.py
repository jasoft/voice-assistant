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


def is_configured() -> bool:
    """TypeSafe 仅在配置了 API key 时启用。"""
    return bool((os.environ.get("TYPESAFE_API_KEY") or "").strip())


def _cfg() -> dict[str, Any]:
    cfg = load_workflow_config().get("typesafe", {})
    return cfg if isinstance(cfg, dict) else {}


def _post_systemone(
    payload: dict[str, Any],
    *,
    api_url: str,
    timeout: float,
    api_key: str,
) -> dict[str, Any] | None:
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                api_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        log(
            f"typesafe: systemone call failed: {type(exc).__name__}: {exc}",
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
) -> dict[str, Any] | None:
    """一次 TypeSafe 调用并行判断两个独立问题：

    - ``intent``：这句话是"记录"还是"其他（询问）"；
    - ``delivery``：期望产出是"paste"（改写/生成一段要插入光标处的内容）
      还是"speak"（常规语音播报回答）。仅当配置了 ``delivery_question`` 时询问。

    有选中文本时 state 用命名字段（instruction + selected_text），让模型能同时
    参考指令与选中内容。Returns ``{"intent": ..., "delivery": ...}`` or ``None``
    when disabled / failed / intent 无法二分 (caller falls back to Harness).
    """
    if not is_configured():
        return None
    cfg = _cfg()
    api_url = str(os.environ.get("TYPESAFE_API_URL") or cfg.get("api_url") or _DEFAULT_API_URL)
    model = str(cfg.get("model") or _DEFAULT_MODEL)
    timeout = float(cfg.get("timeout_seconds", 3.0))
    api_key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
    if not api_key:
        return None

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

    data = _post_systemone(payload, api_url=api_url, timeout=timeout, api_key=api_key)
    if data is None:
        return None

    answers = data.get("answers") or {}
    intent = _choice_answer(answers, "intent", ("record", "other"))
    if intent is None:
        log("typesafe: intent 无法二分，交由上层回退", level="info")
        return None
    delivery = _choice_answer(answers, "delivery", ("paste", "speak"))
    log(f"typesafe: intent={intent} delivery={delivery}", level="info")
    return {"intent": intent, "delivery": delivery}


def ask_is_record(query: str) -> str | None:
    """一次 TypeSafe Choice 调用：判断用户的话是“记录”还是“其他（询问）”。

    Returns ``"record"``, ``"other"``, or ``None`` when disabled / failed
    (caller falls back to the Harness Agent).
    """
    decision = ask_intent_and_delivery(query)
    return decision["intent"] if decision else None
