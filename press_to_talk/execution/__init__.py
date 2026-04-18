from __future__ import annotations

from typing import Any

from .hermes import HermesExecutionRunner
from .intent import IntentExecutionRunner
from .resolver import resolve_execution_mode


def execute_transcript(cfg: Any, transcript: str) -> str:
    mode = resolve_execution_mode(cfg)
    if mode in {"hermes", "memory-chat"}:
        return HermesExecutionRunner(cfg).run(transcript)
    return IntentExecutionRunner(cfg).run(transcript)


def classify_intent(cfg: Any, transcript: str) -> str:
    return IntentExecutionRunner(cfg).classify(transcript)


__all__ = [
    "HermesExecutionRunner",
    "IntentExecutionRunner",
    "classify_intent",
    "execute_transcript",
    "resolve_execution_mode",
]
