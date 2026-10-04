"""Request-scoped text streaming; never replay a completed answer as fake tokens."""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class ReplyStream:
    send: Callable[[str], Awaitable[None]]
    watch: bool = False
    text: str = ""

    async def emit(self, delta: str) -> None:
        if delta:
            self.text += delta
            await self.send(delta)


current_stream: ContextVar[ReplyStream | None] = ContextVar("reply_stream", default=None)


def has_partial_reply() -> bool:
    stream = current_stream.get()
    return bool(stream and stream.text)


def watch_prompt() -> str:
    stream = current_stream.get()
    if not stream or not stream.watch:
        return ""
    from ..utils.env import load_workflow_config
    return str((load_workflow_config().get("prompts", {}).get("watch_answer") or {}).get("system_prompt", ""))


async def complete_reply(client, *, model: str, messages: list):
    """Use actual upstream deltas; only content is public, never reasoning."""
    from ..harness.client import HarnessReply
    stream = current_stream.get()
    if stream and stream.watch:
        messages = [*messages, {"role": "user", "content": watch_prompt()}]
    if stream is None:
        response = await client.chat.completions.create(model=model, messages=messages, max_tokens=4096)
        if not response.choices:
            return None
        message = response.choices[0].message
        reasoning = getattr(message, "reasoning_content", None) or (getattr(message, "model_extra", {}) or {}).get("reasoning_content") or ""
        return HarnessReply(str(message.content or "").strip(), str(reasoning))
    pieces, reasoning = [], []
    finished = False
    response = await client.chat.completions.create(model=model, messages=messages, max_tokens=4096, stream=True)
    try:
        async for chunk in response:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            text = delta.content or ""
            if text:
                pieces.append(text)
                await stream.emit(text)
            thinking = getattr(delta, "reasoning_content", None)
            if thinking:
                reasoning.append(thinking)
            if choice.finish_reason == "stop":
                finished = True
            if choice.finish_reason and choice.finish_reason != "stop":
                raise RuntimeError("回答未完整生成，请重新提问")
    finally:
        await response.close()
    if not finished:
        raise RuntimeError("回答连接中断，请重新提问")
    if not pieces:
        return None
    return HarnessReply("".join(pieces), "".join(reasoning))
