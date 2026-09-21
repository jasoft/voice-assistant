"""TypeSafe (Jev System One) client: millisecond intent & keyword decisions.

A single ``POST /v1/systemone`` call carries mixed questions evaluated in
parallel against the same state:
  - one ``choice`` question for intent (record / find / other)
  - one ``noul`` question per keyword candidate (keep / drop)

All question wording lives in external ``workflow_config.json`` (AGENTS.md rule:
no hard-coded prompts). Every failure degrades gracefully: callers fall back to
the existing regex + LLM path.
"""

from __future__ import annotations

import os
import re
from typing import Any

from ..utils.env import load_workflow_config
from ..utils.logging import log

_DEFAULT_API_URL = "https://api.typesafe.ai/v1/systemone"
_DEFAULT_MODEL = "jev-latest"

# 位置/量词类停用字，用于把过长实词块做二次切分，避免产出碎词候选
_SUB_SPLIT_RE = re.compile(r"(第|个|张|只|台|条|件|里|内|一|二|三|四|五|六|七|八|九|十)")


def is_configured() -> bool:
    """TypeSafe 仅在配置了 API key 时启用。"""
    return bool((os.environ.get("TYPESAFE_API_KEY") or "").strip())


def _cfg() -> dict[str, Any]:
    cfg = load_workflow_config().get("typesafe", {})
    return cfg if isinstance(cfg, dict) else {}


def gen_candidates(query: str, stopwords: list[str], max_candidates: int) -> list[str]:
    """用停用词切分 query 生成候选检索词，长实词块内部二次切分。

    不做 2 字细窗滑窗——碎词（如"书房白"）会被 Jev 误保留，宁缺毋滥。
    """
    if not stopwords:
        stopwords = []
    sep = "|".join(sorted({str(w) for w in stopwords if w}, key=len, reverse=True))
    parts = re.split(sep, re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", query or ""))

    cands: list[str] = []
    for part in parts:
        p = part.strip()
        if len(p) < 2:
            continue
        if len(p) <= 6:
            cands.append(p)
            continue
        # 长实词块：按位置/量词字二次切分
        sub_parts = [s for s in _SUB_SPLIT_RE.split(p) if s and len(s) >= 2]
        for s in sub_parts:
            if len(s) <= 8:
                cands.append(s)
            else:
                # 仍过长：4 字滑窗（步长 3），产生最少的碎词
                for i in range(0, len(s) - 3, 3):
                    cands.append(s[i : i + 4])

    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        if c in seen or len(c) < 2 or c.isdigit():
            continue
        seen.add(c)
        out.append(c)
        if len(out) >= max_candidates:
            break

    # 对 3 字以上候选补头尾 2 字窗口，保证人名/实体词进池。
    # 例：“壮壮喜欢什么”切出 4 字块“壮壮喜欢”后，窗口“壮壮”“喜欢”
    # 才有机会被 Jev 选中；“壮喜”这类中间碎词由 Noul 阈值丢弃。
    for c in out:
        if len(c) >= 3:
            for w in (c[:2], c[-2:]):
                if len(w) >= 2 and w not in seen:
                    seen.add(w)
                    out.append(w)
                    if len(out) >= max_candidates:
                        break
        if len(out) >= max_candidates:
            break
    return out


def ask_intent_and_keywords(query: str) -> dict[str, Any] | None:
    """一次 TypeSafe 调用：意图 Choice + 逐候选词 Noul。

    Returns ``{"intent": str|None, "intent_confidence": float, "keywords": [str]}``
    or ``None`` when disabled / failed (caller falls back).
    """
    if not is_configured():
        return None
    cfg = _cfg()
    api_url = str(os.environ.get("TYPESAFE_API_URL") or cfg.get("api_url") or _DEFAULT_API_URL)
    model = str(cfg.get("model") or _DEFAULT_MODEL)
    threshold = float(cfg.get("noul_threshold", 0.6))
    max_keywords = int(cfg.get("max_keywords", 6))
    max_candidates = int(cfg.get("max_candidates", 14))
    timeout = float(cfg.get("timeout_seconds", 3.0))
    api_key = (os.environ.get("TYPESAFE_API_KEY") or "").strip()
    if not api_key:
        return None

    stopwords = [str(w) for w in cfg.get("candidate_stopwords", [])]
    candidates = gen_candidates(query, stopwords, max_candidates)

    questions: dict[str, Any] = {}
    intent_q = cfg.get("intent_question")
    if isinstance(intent_q, dict):
        questions["intent"] = {
            "type": "choice",
            "instructions": str(intent_q.get("instructions", "")),
            "criteria": {
                str(k): str(v)
                for k, v in (intent_q.get("criteria") or {}).items()
            },
        }
    kw_template = str((cfg.get("keyword_question") or {}).get("instructions_template", ""))
    for index, word in enumerate(candidates):
        questions[f"kw_{index}"] = {
            "type": "noul",
            "instructions": kw_template.replace("%%KEYWORD%%", word),
        }

    payload = {"state": query, "model": model, "questions": questions}

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
    result: dict[str, Any] = {"intent": None, "intent_confidence": 0.0, "keywords": []}

    intent_ans = answers.get("intent")
    if isinstance(intent_ans, dict) and intent_ans.get("type") == "choice":
        result["intent"] = intent_ans.get("choice")
        result["intent_confidence"] = float(intent_ans.get("confidence") or 0.0)

    kept: list[tuple[str, float]] = []
    for key, ans in answers.items():
        if not key.startswith("kw_") or not isinstance(ans, dict):
            continue
        index = int(key.split("_", 1)[1])
        if index >= len(candidates):
            continue
        score = float(ans.get("noul") or 0.0)
        if score >= threshold:
            kept.append((candidates[index], score))
    kept.sort(key=lambda item: item[1], reverse=True)
    result["keywords"] = [word for word, _ in kept[:max_keywords]]

    log(
        f"typesafe: intent={result['intent']} conf={result['intent_confidence']:.2f} "
        f"keywords={result['keywords']}",
        level="info",
    )
    return result
