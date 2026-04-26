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
    photos: List[str] = field(default_factory=list)
    memories: List[dict] = field(default_factory=list)

async def execute_transcript_async(cfg: Any, transcript: str, photo_path: str | None = None) -> ExecutionResult:
    mode = resolve_execution_mode(cfg)
    
    # Initialize Blackboard
    bb = Blackboard(transcript=transcript, cfg=cfg, mode=mode, photo_path=photo_path)
    
    # Build and tick the behavior tree
    tree = build_master_tree()
    await tree.tick(bb)
    
    # --- 统一闭环解析逻辑 ---
    if bb.reply:
        import re
        import json
        from .bt.nodes import get_photo_url
        from ..utils.logging import log
        
        full_reply = bb.reply
        log(f"DEBUG: execution end, reply length={len(full_reply)}", level="debug")
        log(f"DEBUG: memories_raw presence={bb.memories_raw is not None}", level="debug")
        
        # 1. 提取 [SELECTED_IDS: ...]
        match = re.search(r"\[SELECTED_IDS[:：]\s*([^\]]+)\]", full_reply, re.IGNORECASE)
        selected_ids = []
        if match:
            ids_str = match.group(1).strip()
            log(f"DEBUG: found SELECTED_IDS tag: {ids_str}", level="debug")
            if ids_str.lower() != "none":
                import re as regex_split
                selected_ids = [i.strip() for i in regex_split.split(r"[,，]", ids_str)]
            # 清理回复中的标记
            bb.reply = re.sub(r"\[SELECTED_IDS[:：]\s*[^\]]+\]", "", full_reply, flags=re.IGNORECASE).strip()
        
        # 1.5 兜底：如果没 ID 且有搜索结果，尝试模糊匹配
        if not selected_ids and bb.memories_raw:
            try:
                raw_data = json.loads(bb.memories_raw)
                all_items = raw_data.get("results", []) or raw_data.get("items", [])
                log(f"DEBUG: fuzzy matching against {len(all_items)} items", level="debug")
                for item in all_items:
                    mem_text = str(item.get("memory", "")).strip()
                    if mem_text and (mem_text in bb.reply or (len(mem_text) > 5 and mem_text[:len(mem_text)//2] in bb.reply)):
                        selected_ids.append(str(item.get("id")))
                
                log(f"DEBUG: fuzzy matching found {len(selected_ids)} ids", level="debug")
                # 终极兜底：如果还是没选中，且本次有搜到东西，就全给（宁多勿少）
                if not selected_ids:
                     log("DEBUG: final fallback - selecting all items", level="debug")
                     selected_ids = [str(item.get("id")) for item in all_items if item.get("id")]
            except Exception as e:
                log(f"DEBUG: fallback error: {e}", level="warn")
        
        # 2. 反查并填充到 Blackboard (去重处理)
        selected_ids = list(dict.fromkeys(selected_ids))
        log(f"DEBUG: total selected ids for resolution: {len(selected_ids)}", level="debug")
        if selected_ids and bb.memories_raw:
            try:
                raw_data = json.loads(bb.memories_raw)
                items = raw_data.get("results", []) or raw_data.get("items", [])
                item_map = {str(item.get("id")): item for item in items if item.get("id")}
                
                for rid in selected_ids:
                    item = item_map.get(rid)
                    if item:
                        if item not in bb.selected_memories:
                            bb.selected_memories.append(item)
                        path = item.get("photo_path")
                        if path:
                            url = get_photo_url(path)
                            if url and url not in bb.reply_photos:
                                bb.reply_photos.append(url)
                log(f"DEBUG: resolved {len(bb.selected_memories)} items and {len(bb.reply_photos)} photos", level="debug")
            except Exception as e:
                log(f"DEBUG: resolution error: {e}", level="warn")

    if bb.reply:
        return ExecutionResult(reply=bb.reply, photos=bb.reply_photos, memories=bb.selected_memories)
        
    if bb.error:
        return ExecutionResult(reply=f"Error: {bb.error}")

    # Default fallback if tree didn't produce a reply
    return ExecutionResult(reply="I'm sorry, I couldn't process that request.")

def execute_transcript(cfg: Any, transcript: str, photo_path: str | None = None) -> ExecutionResult:
    return asyncio.run(execute_transcript_async(cfg, transcript, photo_path=photo_path))


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
