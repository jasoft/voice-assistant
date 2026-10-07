"""Antigravity native CLI (``agy``) integration.

Per the owner's confirmation the agy CLI is usable directly without any web
login; it is the execution surface for the Antigravity tool. Route: the
official print mode with streamed JSON output
(``agy --project X [--conversation ID] --print TEXT --output-format stream-json``):

- the ``init`` event arrives at startup and carries ``conversation_id`` — the
  executor records it on the server immediately so the user can always resume
  with ``agy --conversation <id>``;
- ``step_update`` events carry real in-turn progress (text deltas);
- the final ``result`` event carries status/response/denied_actions.

Both stdout and stderr are drained concurrently while the process runs (a
chatty child larger than the pipe buffer would otherwise deadlock and fake a
timeout). ``stop_check`` is polled every second; a stop request terminates the
local process ONLY. Whether the remote generation/tool task stops with it is
NOT proven (a model self-report is not acceptable evidence; official cancel
contract or native session status would be required) — the caller must surface
this limitation instead of claiming a native cancellation.

KNOWN LIMIT: CLI conversations do NOT appear in the local Antigravity IDE
Agent Manager conversation list (no supported API for that); this is reported
honestly and the CLI itself remains the interface.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


class AgyClientError(RuntimeError):
    pass


@dataclass
class AgyResult:
    status: str  # "SUCCESS" | "ERROR" | "TIMEOUT" | "STOPPED"
    conversation_id: str | None
    response: str
    denied_actions: list[str] = field(default_factory=list)
    raw_error: str | None = None


def run_agy_turn(
    text: str,
    *,
    project: str,
    conversation_id: str | None = None,
    timeout_seconds: float = 900.0,
    agy_command: str = "agy",
    stop_check: Callable[[], bool] | None = None,
    on_conversation_id: Callable[[str], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> AgyResult:
    argv = [agy_command, "--project", project]
    if conversation_id:
        argv += ["--conversation", conversation_id]
    argv += ["--print", text, "--output-format", "stream-json"]

    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    stdout_lines: list[str] = []
    stderr_tail: list[str] = []
    result_payload: dict[str, Any] | None = None
    seen_conversation_id = conversation_id

    def _handle_event(obj: dict[str, Any]) -> None:
        nonlocal result_payload, seen_conversation_id
        event = obj.get("event")
        if event == "init":
            cid = str((obj.get("init") or {}).get("conversation_id") or obj.get("conversation_id") or "")
            if cid and cid != seen_conversation_id:
                seen_conversation_id = cid
                if on_conversation_id is not None:
                    try:
                        on_conversation_id(cid)
                    except Exception:
                        pass
        elif event == "step_update":
            update = obj.get("step_update") or {}
            cid = str(update.get("conversation_id") or "")
            if cid and cid != seen_conversation_id:
                seen_conversation_id = cid
                if on_conversation_id is not None:
                    try:
                        on_conversation_id(cid)
                    except Exception:
                        pass
            if on_progress is not None and update.get("state") == "ACTIVE":
                delta = str(update.get("text_delta") or "").strip()
                if delta:
                    try:
                        on_progress(delta)
                    except Exception:
                        pass
        elif event == "result":
            result_payload = (obj.get("result") or {})

    def _drain(stream, sink: list[str], is_stdout: bool) -> None:
        for line in stream:
            line = line.rstrip("\n")
            if is_stdout:
                sink.append(line)
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    _handle_event(obj)
            elif len(stderr_tail) < 50:
                sink.append(line)

    drain_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_lines, True), daemon=True)
    drain_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_tail, False), daemon=True)
    drain_out.start()
    drain_err.start()

    stopped = False
    deadline = time.monotonic() + timeout_seconds
    try:
        while proc.poll() is None:
            if stop_check is not None and stop_check():
                stopped = True
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                break
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait(timeout=5)
                return AgyResult(
                    status="TIMEOUT",
                    conversation_id=seen_conversation_id,
                    response="",
                    raw_error=f"agy 执行超时（>{timeout_seconds:.0f}s），本地进程已终止",
                )
            time.sleep(1.0)
    finally:
        # 子进程退出后管道仍有缓冲数据，等待排空线程读完（防止截断 result）
        drain_out.join(timeout=30)
        drain_err.join(timeout=10)

    if stopped:
        return AgyResult(
            status="STOPPED",
            conversation_id=seen_conversation_id,
            response="",
            raw_error="已停止本地 agy 进程；远端任务是否随之终止未证实，请用 agy --conversation 查看实际状态",
        )

    if result_payload is None:
        return AgyResult(
            status="ERROR",
            conversation_id=seen_conversation_id,
            response="",
            raw_error=("\\n".join(stderr_tail[-20:]) or "\\n".join(stdout_lines[-20:]) or f"agy exited with {proc.returncode}"),
        )

    denied = [
        str(action.get("display_name") or action.get("action") or "tool")
        for action in (result_payload.get("denied_actions") or [])
        if isinstance(action, dict)
    ]
    return AgyResult(
        status=str(result_payload.get("status") or "ERROR"),
        conversation_id=str(result_payload.get("conversation_id") or seen_conversation_id or "") or None,
        response=str(result_payload.get("response") or ""),
        denied_actions=denied,
    )
