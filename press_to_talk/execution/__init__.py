from __future__ import annotations

from typing import Any, List

from .hermes import HermesExecutionRunner
from .intent import IntentExecutionRunner
from .memory_chat import MemoryChatExecutionRunner
from .resolver import resolve_execution_mode
from .bt.base import Blackboard
from .bt.builder import build_master_tree


import asyncio

def _run_sync(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    
    # If loop is running, we might be in FastAPI. 
    # But execute_transcript is sync. This is exactly where the error happens.
    # We should ideally use execute_transcript_async in async contexts.
    if loop.is_running():
        # This is a fallback but might still fail in some environments
        # Better to have the caller use execute_transcript_async
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    else:
        return asyncio.run(coro)

from dataclasses import dataclass, field

@dataclass
class ExecutionResult:
    reply: str
    memories: List[dict] = field(default_factory=list)
    query: Optional[str] = None
    debug_info: Optional[dict] = None

async def execute_transcript_async(
    cfg: Any, 
    transcript: str, 
    photo_path: str | None = None,
    session_id: str = "",
    started_at: str = "",
    peak_level: float = 0.0,
    mean_level: float = 0.0,
    session_mode: str = "cli"
) -> ExecutionResult:
    mode = resolve_execution_mode(cfg)
    
    # Initialize Blackboard
    bb = Blackboard(
        transcript=transcript, 
        cfg=cfg, 
        mode=mode, 
        photo_path=photo_path,
        session_id=session_id,
        started_at=started_at,
        peak_level=peak_level,
        mean_level=mean_level,
        session_mode=session_mode
    )
    
    # Build and tick the behavior tree
    tree = build_master_tree()
    await tree.tick(bb)
    
    if bb.reply:
        return ExecutionResult(reply=bb.reply, memories=bb.memories, query=bb.query, debug_info=bb.debug_info)

    if bb.error:
        return ExecutionResult(reply=f"Error: {bb.error}", debug_info=bb.debug_info)

    # Default fallback if tree didn't produce a reply
    return ExecutionResult(reply="I'm sorry, I couldn't process that request.", debug_info=bb.debug_info)

def execute_transcript(
    cfg: Any, 
    transcript: str, 
    photo_path: str | None = None,
    session_id: str = "",
    started_at: str = "",
    peak_level: float = 0.0,
    mean_level: float = 0.0,
    session_mode: str = "cli"
) -> ExecutionResult:
    return asyncio.run(execute_transcript_async(
        cfg, 
        transcript, 
        photo_path=photo_path,
        session_id=session_id,
        started_at=started_at,
        peak_level=peak_level,
        mean_level=mean_level,
        session_mode=session_mode
    ))


def classify_intent(cfg: Any, transcript: str) -> str:
    return IntentExecutionRunner(cfg).classify(transcript)


__all__ = [
    "HermesExecutionRunner",
    "IntentExecutionRunner",
    "MemoryChatExecutionRunner",
    "ExecutionResult",
    "classify_intent",
    "execute_transcript",
    "execute_transcript_async",
    "resolve_execution_mode",
]
