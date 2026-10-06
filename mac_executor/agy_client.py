"""Antigravity native CLI (``agy``) integration.

Verified capabilities on this machine (agy 1.3.0, 2026-10-07):

- ``agy --project <name> --print <text> --output-format json`` creates a real
  cloud conversation and returns ``conversation_id``;
- the SAME conversation continues via
  ``agy --project <name> --conversation <id> --print ...`` (verified: the model
  recalls its previous answer across invocations);
- headless mode cannot prompt for tool permissions: commands are auto-denied
  and reported in ``denied_actions`` — we surface that honestly as a failure
  with an explanatory note instead of bypassing protections;
- KNOWN LIMIT (recorded in docs/voice-project-entry-verification.md): CLI
  conversations do NOT appear in the local Antigravity IDE Agent Manager
  conversation list (no supported API for that); the supported remote entry is
  the ``--remote-control`` console (antigravity.google.com).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


class AgyClientError(RuntimeError):
    pass


@dataclass
class AgyResult:
    status: str  # "SUCCESS" | "ERROR" | "TIMEOUT"
    conversation_id: str | None
    response: str
    denied_actions: list[str]
    raw_error: str | None = None


def run_agy_turn(
    text: str,
    *,
    project: str,
    conversation_id: str | None = None,
    timeout_seconds: float = 900.0,
    agy_command: str = "agy",
) -> AgyResult:
    argv = [agy_command, "--project", project, "--print", text, "--output-format", "json"]
    if conversation_id:
        argv[2:2] = ["--conversation", conversation_id]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return AgyResult(status="TIMEOUT", conversation_id=conversation_id, response="", denied_actions=[], raw_error="agy 执行超时")
    stdout = proc.stdout or ""
    stderr = (proc.stderr or "").strip()
    payload: dict[str, Any] | None = None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "conversation_id" in candidate:
            payload = candidate
            break
    if payload is None:
        return AgyResult(
            status="ERROR",
            conversation_id=conversation_id,
            response="",
            denied_actions=[],
            raw_error=stderr[-800:] or stdout[-800:] or f"agy exited with {proc.returncode}",
        )
    denied = [
        str(action.get("display_name") or action.get("action") or "tool")
        for action in (payload.get("denied_actions") or [])
        if isinstance(action, dict)
    ]
    return AgyResult(
        status=str(payload.get("status") or "ERROR"),
        conversation_id=str(payload.get("conversation_id") or conversation_id or "") or None,
        response=str(payload.get("response") or ""),
        denied_actions=denied,
    )
