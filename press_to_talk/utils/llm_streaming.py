from __future__ import annotations

import asyncio
import os
from typing import Any, Optional, Callable


def llm_idle_timeout_seconds() -> float:
    return float(os.environ.get("PTT_LLM_IDLE_TIMEOUT_SECONDS", "8"))


async def stream_chat_completion_text(
    client: Any,
    *,
    idle_timeout: float | None = None,
    callback: Optional[Callable[[str], None]] = None,
    **kwargs: Any,
) -> str:
    """Return streamed chat text, resetting timeout after each received chunk."""
    timeout = llm_idle_timeout_seconds() if idle_timeout is None else float(idle_timeout)
    stream = await client.chat.completions.create(stream=True, **kwargs)
    
    # Handle non-streaming fallback if necessary (some proxies/APIs might ignore stream=True)
    choices = getattr(stream, "choices", None) or []
    if choices and getattr(choices[0], "message", None) is not None:
        content = str(getattr(choices[0].message, "content", "") or "")
        if callback:
            callback(content)
        return content

    iterator = stream.__aiter__()
    chunks: list[str] = []

    while True:
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=timeout)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            if chunks:
                break
            raise

        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        content = getattr(delta, "content", None) if delta is not None else None
        if content:
            text = str(content)
            chunks.append(text)
            if callback:
                callback(text)

    return "".join(chunks)
