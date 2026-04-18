from __future__ import annotations

from typing import Any

from .hermes import HermesExecutionRunner
from .intent import IntentExecutionRunner
from .memory_chat import MemoryChatExecutionRunner
from .resolver import resolve_execution_mode


def execute_transcript(cfg: Any, transcript: str) -> str:
    mode = resolve_execution_mode(cfg)
    if mode == "hermes":
        return HermesExecutionRunner(cfg).run(transcript)
    if mode == "memory-chat":
        return MemoryChatExecutionRunner(cfg).run(transcript)
    return IntentExecutionRunner(cfg).run(transcript)


def classify_intent(cfg: Any, transcript: str) -> str:
    return IntentExecutionRunner(cfg).classify(transcript)


__all__ = [
    "HermesExecutionRunner",
    "IntentExecutionRunner",
    "MemoryChatExecutionRunner",
    "classify_intent",
    "execute_transcript",
    "resolve_execution_mode",
]
