"""Codex native integration via the official app-server JSON-RPC protocol.

The executor keeps ONE long-lived ``codex app-server`` child process. Threads
are persisted by Codex itself into the shared on-disk thread store that the
Codex IDE/desktop reads, so every session created here shows up in the user's
Codex conversation list and continues there (verified 2026-10-07, thread
01a1121d-3909-7862-bb90-4ec91120d4fd). Continuation always goes through
``thread/resume`` with the recorded native thread id — never "the most recent
session" — so the executor, the IDE and manual follow-ups share one context.

Turns run under Codex's own sandbox: workspace-write limited to the project
directory, network enabled, approval policy ``never`` (requests that would need
interactive approval are denied by the tool instead of silently bypassed).
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable


class CodexClientError(RuntimeError):
    pass


@dataclass
class TurnResult:
    status: str  # "completed" | "interrupted" | "failed"
    final_text: str = ""
    error: str | None = None
    turn_id: str | None = None


@dataclass
class _PendingRequest:
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: str | None = None


class CodexAppServerClient:
    def __init__(self, codex_command: str = "codex", client_name: str = "voice-project-executor"):
        self._argv = [codex_command, "app-server"]
        self._client_name = client_name
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._pending: dict[int, _PendingRequest] = {}
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._start_lock = threading.Lock()

    # -- process / protocol -------------------------------------------------

    def start(self) -> None:
        with self._start_lock:
            if self._proc and self._proc.poll() is None:
                return
            self._proc = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            self._rpc("initialize", {"clientInfo": {"name": self._client_name, "title": self._client_name, "version": "0.1"}})
            self._notify("initialized", {})

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    def _ensure_running(self) -> subprocess.Popen[str]:
        if self._proc is None or self._proc.poll() is not None:
            self.start()
        assert self._proc is not None and self._proc.stdin is not None
        return self._proc

    def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message.get("id"), int) and ("result" in message or "error" in message):
                pending = self._pending.pop(message["id"], None)
                if pending is not None:
                    if "error" in message and message["error"] is not None:
                        pending.error = json.dumps(message["error"], ensure_ascii=False)
                    else:
                        pending.result = message.get("result")
                    pending.event.set()
            elif message.get("method"):
                self._events.put(message)

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        proc = self._ensure_running()
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        try:
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise CodexClientError(f"codex app-server 通道已断开: {exc}") from exc

    def _rpc(self, method: str, params: dict[str, Any], timeout: float = 30.0) -> Any:
        proc = self._ensure_running()
        with self._id_lock:
            request_id = self._next_id
            self._next_id += 1
        pending = _PendingRequest()
        self._pending[request_id] = pending
        payload = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._pending.pop(request_id, None)
            raise CodexClientError(f"codex app-server 通道已断开: {exc}") from exc
        if not pending.event.wait(timeout):
            self._pending.pop(request_id, None)
            raise CodexClientError(f"codex app-server 请求超时: {method}")
        if pending.error is not None:
            raise CodexClientError(f"codex app-server 错误 ({method}): {pending.error}")
        return pending.result

    # -- threads / turns -----------------------------------------------------

    def start_thread(self, cwd: str) -> str:
        result = self._rpc("thread/start", {"cwd": cwd}, timeout=60.0)
        thread = (result or {}).get("thread") or {}
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            raise CodexClientError("thread/start 未返回线程 id")
        return thread_id

    def resume_thread(self, thread_id: str) -> None:
        self._rpc("thread/resume", {"threadId": thread_id}, timeout=60.0)

    def run_turn(
        self,
        thread_id: str,
        text: str,
        *,
        cwd: str,
        sandbox: str = "workspace-write",
        network_access: bool = True,
        approval_policy: str = "never",
        stop_check: Callable[[], bool] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> TurnResult:
        """Run one turn to completion; ``stop_check`` is polled between events
        and triggers ``turn/interrupt`` when it returns True."""
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text}],
            "cwd": cwd,
            "approvalPolicy": approval_policy,
            "sandboxPolicy": {
                "type": "workspaceWrite" if sandbox == "workspace-write" else sandbox,
                "writableRoots": [cwd],
                "networkAccess": network_access,
            },
        }
        result = self._rpc("turn/start", params, timeout=60.0)
        turn = (result or {}).get("turn") or {}
        turn_id = str(turn.get("id") or "")
        if not turn_id:
            raise CodexClientError("turn/start 未返回 turn id")

        final_text = ""
        last_message = ""
        interrupt_sent = False
        while True:
            if stop_check is not None and not interrupt_sent and stop_check():
                self.interrupt(thread_id, turn_id)
                interrupt_sent = True
            try:
                message = self._events.get(timeout=0.5)
            except queue.Empty:
                continue
            method = str(message.get("method") or "")
            mparams = message.get("params") or {}
            if mparams.get("threadId") not in (None, thread_id):
                if method not in ("turn/completed", "turn/failed"):
                    if on_event is None:
                        continue
                else:
                    continue
            if on_event is not None:
                try:
                    on_event(message)
                except Exception:
                    pass
            if method == "item/completed":
                item = mparams.get("item") or {}
                if item.get("type") == "agentMessage":
                    text_out = str(item.get("text") or "")
                    if item.get("phase") == "final_answer" or not last_message:
                        final_text = text_out
                    last_message = text_out
            elif method in ("turn/completed", "turn/failed") and str((mparams.get("turn") or {}).get("id")) == turn_id:
                turn_info = mparams.get("turn") or {}
                status = str(turn_info.get("status") or method)
                error = turn_info.get("error")
                if interrupt_sent:
                    return TurnResult(status="interrupted", final_text=final_text or last_message, turn_id=turn_id)
                if method == "turn/failed" or status == "failed":
                    return TurnResult(
                        status="failed",
                        final_text=final_text or last_message,
                        error=json.dumps(error, ensure_ascii=False) if error else "turn failed",
                        turn_id=turn_id,
                    )
                return TurnResult(status="completed", final_text=final_text or last_message, turn_id=turn_id)

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        self._rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=15.0)
