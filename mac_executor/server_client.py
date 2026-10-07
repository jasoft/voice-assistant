"""HTTP client for the voice-assistant server project-task API."""

from __future__ import annotations

from typing import Any

import httpx


class ServerClient:
    def __init__(self, *, base_url: str, token: str, timeout: float = 15.0):
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        resp = httpx.post(
            f"{self._base_url}{path}",
            json=payload if payload is not None else {},
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None

    def claim(self, *, executor_id: str, tools: list[str]) -> dict[str, Any] | None:
        return self._post("/v1/project-tasks/claim", {"executor_id": executor_id, "tools": tools})

    def heartbeat(self, *, executor_id: str, tools: list[str]) -> None:
        self._post("/v1/project-tasks/heartbeat", {"executor_id": executor_id, "tools": tools})

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        resp = httpx.get(
            f"{self._base_url}/v1/project-tasks/{task_id}",
            headers=self._headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None

    def touch_task(self, task_id: str) -> dict[str, Any] | None:
        """Task-level keepalive: refresh the task's updated_at so the server's
        stale sweep knows THIS task is still being worked (heartbeat alone
        proves the process lives, not the task)."""
        return self._post(f"/v1/project-tasks/{task_id}/events", {})

    def event(
        self,
        task_id: str,
        *,
        status: str | None = None,
        native_session_id: str | None = None,
        result: str | None = None,
        error: str | None = None,
        note: str | None = None,
        applied_followups: int | None = None,
        requirement_applied: bool | None = None,
    ) -> dict[str, Any] | None:
        payload = {k: v for k, v in {
            "status": status,
            "native_session_id": native_session_id,
            "result": result,
            "error": error,
            "note": note,
            "applied_followups": applied_followups,
            "requirement_applied": requirement_applied,
        }.items() if v is not None}
        return self._post(f"/v1/project-tasks/{task_id}/events", payload)
