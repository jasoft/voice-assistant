"""TypeSafe (Jev System One) client: millisecond record/other decision.

A single ``POST /v1/systemone`` call evaluates one choice question:
  - is this utterance a "record" (保存信息) or "other" (询问/闲聊)?

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


def ask_is_record(query: str) -> str | None:
    """一次 TypeSafe Choice 调用：判断用户的话是“记录”还是“其他（询问）”。

    Returns ``"record"``, ``"other"``, or ``None`` when disabled / failed
    (caller falls back to the Harness Agent).
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
    payload = {
        "state": query,
        "model": model,
        "questions": {
            "intent": {
                "type": "choice",
                "instructions": str(intent_q.get("instructions", "")),
                "criteria": {
                    str(k): str(v)
                    for k, v in (intent_q.get("criteria") or {}).items()
                },
            }
        },
    }

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
            data = resp.json()
    except Exception as exc:
        log(
            f"typesafe: systemone call failed: {type(exc).__name__}: {exc}",
            level="warn",
        )
        return None

    answers = data.get("answers") or {}
    intent = None
    intent_ans = answers.get("intent")
    if isinstance(intent_ans, dict) and intent_ans.get("type") == "choice":
        choice = intent_ans.get("choice")
        if choice in ("record", "other"):
            intent = choice
    log(f"typesafe: intent={intent}", level="info")
    return intent
