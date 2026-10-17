"""Request-scoped text streaming; never replay a completed answer as fake tokens."""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class ReplyStream:
    send: Callable[[str], Awaitable[None]]
    watch: bool = False
    text: str = ""
    emitted_text: str = ""
    _pending: str = ""
    _spoken_started: bool = False

    async def emit(self, delta: str) -> None:
        if not delta:
            return
        self.text += delta
        if self._spoken_started:
            return
        self._pending += delta
        await self._flush_safe(is_eof=False)

    async def finish(self) -> None:
        """流结束时调用，将缓冲区内安全的尾部内容刷出。"""
        if self._spoken_started:
            self._pending = ""
            return
        await self._flush_safe(is_eof=True)

    async def _flush_safe(self, is_eof: bool = False) -> None:
        if self._spoken_started:
            self._pending = ""
            return

        # 1. 检查 pending 中是否已包含完整的 <spoken> 标签
        lower_pending = self._pending.lower()
        tag_pos = lower_pending.find("<spoken>")
        if tag_pos != -1:
            safe_text = self._pending[:tag_pos].rstrip("\r\n \t")
            if safe_text:
                await self.send(safe_text)
                self.emitted_text += safe_text
            self._spoken_started = True
            self._pending = ""
            return

        if is_eof:
            # 流已结束且未遇到完整 <spoken>：若尾部残留疑似标签前缀，裁掉
            last_lt = self._pending.rfind("<")
            if last_lt != -1 and "<spoken>".startswith(self._pending[last_lt:].lower()):
                self._pending = self._pending[:last_lt]
            safe_text = self._pending.rstrip("\r\n \t")
            if safe_text:
                await self.send(safe_text)
                self.emitted_text += safe_text
            self._pending = ""
            return

        # 2. 检查 pending 尾部是否包含疑似 <spoken> 前缀（例如 "<", "<sp", "<spoken"）
        last_lt = self._pending.rfind("<")
        if last_lt != -1:
            tail = self._pending[last_lt:].lower()
            if "<spoken>".startswith(tail):
                prefix_candidate = self._pending[:last_lt].rstrip("\r\n \t")
                if prefix_candidate:
                    await self.send(prefix_candidate)
                    self.emitted_text += prefix_candidate
                    self._pending = self._pending[len(prefix_candidate):]
                return

        # 3. 检查 pending 尾部是否为纯空白字符（可能紧邻后续到来的 <spoken>）
        # 在非空白字符到来之前暂扣尾随空白
        trailing_ws_len = len(self._pending) - len(self._pending.rstrip("\r\n \t"))
        if trailing_ws_len > 0:
            safe_text = self._pending[:-trailing_ws_len]
            if safe_text:
                await self.send(safe_text)
                self.emitted_text += safe_text
                self._pending = self._pending[len(safe_text):]
            return

        # 4. 无疑似标签和尾随空白，全部安全下发
        if self._pending:
            await self.send(self._pending)
            self.emitted_text += self._pending
            self._pending = ""


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
        messages = [{"role": "system", "content": watch_prompt()}, *messages]
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
    if stream:
        await stream.finish()
    if not finished:
        raise RuntimeError("回答连接中断，请重新提问")
    if not pieces:
        return None
    return HarnessReply("".join(pieces), "".join(reasoning))
