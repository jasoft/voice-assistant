from __future__ import annotations

from typing import Any

from .hermes import HermesExecutionRunner
from .intent import IntentExecutionRunner
from .memory_chat import MemoryChatExecutionRunner
from .resolver import resolve_execution_mode
from .bt.base import Blackboard
from .bt.builder import build_master_tree


def execute_transcript(cfg: Any, transcript: str) -> str:
    mode = resolve_execution_mode(cfg)
    
    # Initialize Blackboard
    bb = Blackboard(transcript=transcript, cfg=cfg, mode=mode)
    
    # Build and tick the behavior tree
    tree = build_master_tree()
    tree.tick(bb)
    
    if bb.reply:
        return bb.reply
        
    if bb.error:
        return f"Error: {bb.error}"

    # Default fallback if tree didn't produce a reply (though LLMChatFallbackAction should)
    return "I'm sorry, I couldn't process that request."


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
