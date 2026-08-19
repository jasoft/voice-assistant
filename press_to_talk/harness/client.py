from __future__ import annotations

import asyncio
import base64
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx


class HarnessError(RuntimeError):
    """Raised when DeepSeek Harness cannot accept or complete a request."""


JsonRequester = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class DeepSeekHarnessClient:
    """Small async client for the DeepSeek Harness Web API.

    Harness owns the agent, tools, and Mem0 access. This class only handles the
    session/prompt/history transport needed by the voice-assistant API.
    """

    def __init__(
        self,
        api_url: str,
        *,
        agent_preset: str = "memo-mem0",
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 0.25,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_url = api_url.strip().rstrip("/")
        if not normalized_url:
            raise HarnessError("DeepSeek Harness API 地址为空")
        self.api_url = normalized_url
        self.agent_preset = agent_preset.strip()
        self.timeout_seconds = max(1.0, timeout_seconds)
        self.poll_interval_seconds = max(0.05, poll_interval_seconds)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            headers=self._default_headers(),
        )
        self._session_id: str | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> "DeepSeekHarnessClient":
        return cls(
            os.environ.get("PTT_HARNESS_API_URL", "http://127.0.0.1:3080"),
            agent_preset=os.environ.get("PTT_HARNESS_AGENT_PRESET", "memo-mem0"),
            timeout_seconds=float(os.environ.get("PTT_HARNESS_TIMEOUT_SECONDS", "60")),
            poll_interval_seconds=float(os.environ.get("PTT_HARNESS_POLL_INTERVAL_SECONDS", "0.25")),
        )

    def _default_headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        token = os.environ.get("PTT_HARNESS_API_TOKEN", "").strip()
        if token:
            headers["authorization"] = f"Bearer {token}"
        return headers

    async def close(self) -> None:
        await self._client.aclose()

    async def query(self, text: str, *, photo: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one user turn and wait for Harness to persist its final reply."""
        prompt = str(text or "").strip()
        if not prompt:
            raise HarnessError("查询内容不能为空")

        async with self._lock:
            session_id = await self._ensure_session()
            baseline = await self._history(session_id)
            baseline_seq = max((self._event_seq(entry) for entry in baseline), default=-1)

            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            image_part = self._photo_content(photo)
            if image_part is not None:
                content.append(image_part)

            await self._rpc(
                "session.prompt",
                {
                    "sessionId": session_id,
                    "mode": "queue",
                    "content": content,
                },
            )

            reply = await self._wait_for_reply(session_id, baseline_seq)
            return {
                "reply": reply,
                "memories": [],
                "query": prompt,
                "debug_info": {
                    "backend": "deepseek-harness",
                    "agent_preset": self.agent_preset,
                    "session_id": session_id,
                },
            }

    async def _ensure_session(self) -> str:
        if self._session_id:
            return self._session_id

        payload: dict[str, Any] = {}
        if self.agent_preset:
            payload["agentPreset"] = self.agent_preset
        value = await self._rpc("session.create", payload)
        session_id = str(value.get("sessionId", "")).strip()
        if not session_id:
            raise HarnessError("DeepSeek Harness 创建会话成功但没有返回 sessionId")
        self._session_id = session_id
        return session_id

    async def _history(self, session_id: str) -> list[dict[str, Any]]:
        value = await self._rpc(
            "session.history",
            {"sessionId": session_id, "maxMessages": 200},
        )
        events = value.get("events", [])
        return [entry for entry in events if isinstance(entry, dict)]

    async def _wait_for_reply(self, session_id: str, baseline_seq: int) -> str:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            try:
                entries = await self._history(session_id)
            except HarnessError as exc:
                if "session-not-found" in str(exc):
                    self._session_id = None
                raise

            candidates = [
                entry
                for entry in entries
                if self._event_seq(entry) > baseline_seq
                and self._event(entry).get("type") == "assistant/message"
            ]
            if candidates:
                reply = self._assistant_text(candidates[-1])
                if reply:
                    return reply
                # An assistant/message with no text can be an intermediate
                # tool-call/reasoning boundary. Keep polling for the final
                # text message instead of mistaking that boundary for the
                # completed answer.

            failure = self._turn_failure(entries, baseline_seq)
            if failure:
                raise HarnessError(f"DeepSeek Harness Agent 执行失败：{failure}")

            await asyncio.sleep(self.poll_interval_seconds)

        raise HarnessError(f"等待 DeepSeek Harness 回复超时（{self.timeout_seconds:.1f} 秒）")

    async def _rpc(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        rpc_id = uuid.uuid4().hex
        try:
            response = await self._client.post(
                f"{self.api_url}/api/{method}",
                json={
                    "type": "client-request",
                    "rpcId": rpc_id,
                    "method": method,
                    "payload": payload,
                },
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise HarnessError(f"DeepSeek Harness API 请求失败：{exc}") from exc
        except ValueError as exc:
            raise HarnessError("DeepSeek Harness API 返回了无效 JSON") from exc

        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            raise HarnessError("DeepSeek Harness API 返回缺少 result")
        if result.get("ok") is not True:
            error = result.get("error")
            if isinstance(error, dict):
                code = str(error.get("code", "unknown"))
                message = str(error.get("message", "请求被拒绝"))
                raise HarnessError(f"{code}: {message}")
            raise HarnessError("DeepSeek Harness API 请求被拒绝")

        value = result.get("value", {})
        if not isinstance(value, dict):
            raise HarnessError(f"DeepSeek Harness {method} 返回格式错误")
        return value

    @staticmethod
    def _event(entry: dict[str, Any]) -> dict[str, Any]:
        event = entry.get("event")
        return event if isinstance(event, dict) else {}

    @classmethod
    def _event_seq(cls, entry: dict[str, Any]) -> int:
        value = cls._event(entry).get("seq", -1)
        return int(value) if isinstance(value, (int, float)) else -1

    @classmethod
    def _assistant_text(cls, entry: dict[str, Any]) -> str:
        event = cls._event(entry)
        data = event.get("data")
        if not isinstance(data, dict):
            return ""
        message = data.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return ""
        content = message.get("content")
        if not isinstance(content, list):
            return ""
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        ]
        return "".join(parts).strip()

    @classmethod
    def _turn_failure(cls, entries: list[dict[str, Any]], baseline_seq: int) -> str:
        """Return a terminal Harness turn error instead of waiting for a reply."""

        for entry in entries:
            if cls._event_seq(entry) <= baseline_seq:
                continue
            event = cls._event(entry)
            if event.get("type") != "turn/end":
                continue
            data = event.get("data")
            reason = data.get("reason") if isinstance(data, dict) else None
            if not isinstance(reason, dict) or reason.get("kind") != "error":
                continue
            error = reason.get("error")
            if isinstance(error, dict):
                message = str(error.get("message", "未知错误")).strip()
                if message:
                    return message
            return "未知错误"
        return ""

    @staticmethod
    def _photo_content(photo: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(photo, dict):
            return None
        media_type = str(photo.get("mime") or "image/jpeg").strip().lower()
        if media_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            media_type = "image/jpeg"

        raw_data = str(photo.get("data") or "").strip()
        if raw_data:
            if raw_data.startswith("data:") and ";base64," in raw_data:
                prefix, raw_data = raw_data.split(";base64,", 1)
                media_type = prefix.removeprefix("data:") or media_type
            try:
                base64.b64decode(raw_data, validate=True)
            except (ValueError, base64.binascii.Error):
                raise HarnessError("图片附件不是有效的 Base64 数据")
            return {"type": "image", "mediaType": media_type, "data": raw_data}

        return None
