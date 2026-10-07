"""Mac 执行器单元测试：Codex 客户端协议循环（用假进程注入事件流）与 agy 输出解析。"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from types import SimpleNamespace

import pytest

from mac_executor import agy_client
from mac_executor.codex_client import CodexAppServerClient, CodexClientError, TurnResult
from mac_executor.config import load_registry, load_tool_availability
from mac_executor.daemon import build_turn_text


class FakeProc:
    """按请求回应的假 codex app-server 进程：stdin.write 收到请求后把对应
    响应/事件推入队列，stdout 以阻塞生成器读出——与真实管道时序一致。"""

    def __init__(self, responses_by_method):
        self.responses = responses_by_method
        self.requests: list[dict] = []
        self._q: queue.Queue = queue.Queue()
        self.stdin = SimpleNamespace(write=self._write, flush=lambda: None, close=lambda: None)
        self.pid = 0

    def _write(self, payload):
        message = json.loads(payload)
        self.requests.append(message)
        for out in self.responses.get(str(message.get("method")), []):
            self._q.put(json.dumps(out) + "\n")
        return len(payload)

    @property
    def stdout(self):
        def gen():
            while True:
                item = self._q.get()
                if item is None:
                    return
                yield item
        return gen()

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self._q.put(None)


def _responses_for_turn(thread_id, turn_id, final_text):
    return {
        "initialize": [{"jsonrpc": "2.0", "id": 1, "result": {"userAgent": "fake"}}],
        "turn/start": [
            {"jsonrpc": "2.0", "id": 2, "result": {"turn": {"id": turn_id}}},
            {"method": "item/completed", "params": {"threadId": thread_id, "item": {"type": "agentMessage", "text": "目录名：va", "phase": "commentary"}}},
            {"method": "item/completed", "params": {"threadId": thread_id, "item": {"type": "agentMessage", "text": final_text, "phase": "final_answer"}}},
            {"method": "turn/completed", "params": {"threadId": thread_id, "turn": {"id": turn_id, "status": "completed"}}},
        ],
        "turn/interrupt": [{"jsonrpc": "2.0", "id": 3, "result": {}}],
    }


def test_run_turn_collects_final_answer(tmp_path, monkeypatch):
    fake = FakeProc(_responses_for_turn("thread-1", "turn-1", "目录名：va\n分支：main"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
    client = CodexAppServerClient()
    result = client.run_turn("thread-1", "只读检查", cwd=str(tmp_path))
    assert isinstance(result, TurnResult)
    assert result.status == "completed"
    assert result.final_text == "目录名：va\n分支：main"
    assert result.turn_id == "turn-1"
    turn_requests = [m for m in fake.requests if m.get("method") == "turn/start"]
    assert turn_requests and turn_requests[0]["params"]["input"][0]["text"] == "只读检查"
    assert turn_requests[0]["params"]["cwd"] == str(tmp_path)


def test_run_turn_stop_check_interrupts(tmp_path, monkeypatch):
    fake = FakeProc(_responses_for_turn("thread-1", "turn-1", "部分结果"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
    client = CodexAppServerClient()
    result = client.run_turn("thread-1", "长任务", cwd=str(tmp_path), stop_check=lambda: True)
    assert result.status == "interrupted"
    assert any(m.get("method") == "turn/interrupt" for m in fake.requests)


def test_thread_start_requires_id(monkeypatch):
    fake = FakeProc({
        "initialize": [{"jsonrpc": "2.0", "id": 1, "result": {}}],
        "thread/start": [{"jsonrpc": "2.0", "id": 2, "result": {"thread": {}}}],
    })
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
    client = CodexAppServerClient()
    with pytest.raises(CodexClientError):
        client.start_thread("/tmp/x")


def _fake_agy_proc(monkeypatch, stdout_text, stderr_text="", hang_first=False):
    captured = {}
    state = {"waits": 0}

    class FakeProc:
        pid = 1
        returncode = 0
        stdout = SimpleNamespace(read=lambda: stdout_text)
        stderr = SimpleNamespace(read=lambda: stderr_text)
        terminated = False

        def wait(self, timeout=None):
            if hang_first and state["waits"] == 0:
                state["waits"] += 1
                raise subprocess.TimeoutExpired(cmd="agy", timeout=timeout or 1)
            return 0

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

    def fake_popen(argv, **kw):
        captured["argv"] = argv
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return captured


def _fake_agy_proc(monkeypatch, stdout_text, stderr_text="", hang_first=False):
    captured = {}
    state = {"waits": 0}

    class FakeProc:
        pid = 1
        returncode = 0
        terminated = False

        def __init__(self):
            self.stdout = iter(stdout_text.splitlines(True))
            self.stderr = iter(stderr_text.splitlines(True))

        def wait(self, timeout=None):
            if hang_first and state["waits"] == 0:
                state["waits"] += 1
                raise subprocess.TimeoutExpired(cmd="agy", timeout=timeout or 1)
            return 0

        def poll(self):
            return 0  # 子进程立即退出，输出由 drain 线程消费

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

    def fake_popen(argv, **kw):
        captured["argv"] = argv
        proc = FakeProc()
        return proc

    # stdout 用行迭代：让 FakeProc.stdout 可迭代
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return captured


def test_agy_stream_json_parsing(monkeypatch):
    """init/step_update/result 事件流：早记录会话 ID、结果解析、无 --remote-control 前提。"""
    lines = "\n".join([
        json.dumps({"event": "init", "conversation_id": "conv-123", "init": {"cwd": "/tmp"}}),
        json.dumps({"event": "step_update", "step_update": {"conversation_id": "conv-123", "step_index": 1, "state": "ACTIVE", "text_delta": "ok"}}),
        json.dumps({"event": "result", "result": {"conversation_id": "conv-123", "status": "SUCCESS", "response": "ok\n", "denied_actions": [{"action": "command", "display_name": "RunCommand"}]}}),
    ]) + "\n"
    captured = _fake_agy_proc(monkeypatch, lines)
    seen_cid = []
    result = agy_client.run_agy_turn("只读检查", project="voice-assistant", on_conversation_id=seen_cid.append)
    assert result.status == "SUCCESS"
    assert result.conversation_id == "conv-123"
    assert result.denied_actions == ["RunCommand"]
    assert seen_cid == ["conv-123"]  # init 即早记录
    assert "--remote-control" not in captured["argv"]
    assert "--output-format" in captured["argv"] and "stream-json" in captured["argv"]

    result2 = agy_client.run_agy_turn("继续", project="voice-assistant", conversation_id="conv-123")
    idx = captured["argv"].index("--conversation")
    assert captured["argv"][idx + 1] == "conv-123"
    assert captured["argv"][idx + 2] == "--print"
    assert captured["argv"][-2:] == ["--output-format", "stream-json"]
    assert result2.response == "ok\n"


def test_agy_no_json_output(monkeypatch):
    _fake_agy_proc(monkeypatch, "plain text", stderr_text="boom")
    result = agy_client.run_agy_turn("x", project="p")
    assert result.status == "ERROR" and "boom" in (result.raw_error or "")


def test_agy_stop_check_terminates_and_records_early_cid(monkeypatch):
    """停止：终止本地进程返回 STOPPED；且会话 ID 已从 init 早记录（用户可续接）。"""
    lines = "\n".join([
        json.dumps({"event": "init", "conversation_id": "conv-stop", "init": {}}),
    ]) + "\n"
    state = {"waits": 0, "terminated": False}

    class StopProc:
        pid = 1
        returncode = -15
        stdout = iter(lines.splitlines(True))
        stderr = iter([])

        def wait(self, timeout=None):
            if state["waits"] == 0:
                state["waits"] += 1
                raise subprocess.TimeoutExpired(cmd="agy", timeout=timeout or 1)
            return 0

        def poll(self):
            return None

        def terminate(self):
            state["terminated"] = True

        def kill(self):
            state["terminated"] = True

    def fake_popen(argv, **kw):
        return StopProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    result = agy_client.run_agy_turn("x", project="p", stop_check=lambda: True)
    assert result.status == "STOPPED"
    assert result.conversation_id == "conv-stop"  # 早记录，停止后仍可续接
    assert state["terminated"]


def test_agy_large_output_no_deadlock(monkeypatch):
    """真实子进程回归：输出超过管道容量（数 MB 日志）不得堵死导致假超时。"""
    import tempfile as _tempfile
    import os as _os

    junk = "x" * 200 + "\n"
    big_log = junk * 12000  # ~2.4MB
    payload = json.dumps({"event": "result", "result": {
        "conversation_id": "c-big", "status": "SUCCESS", "response": "done", "denied_actions": []}})
    script = _tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False)
    script.write(f"#!/bin/sh\nprintf '%s' '{big_log}'\nprintf '%s\\n' '{payload}'\n")
    script.close()
    _os.chmod(script.name, 0o755)
    result = agy_client.run_agy_turn("x", project="p", agy_command=script.name, timeout_seconds=60)
    _os.unlink(script.name)
    assert result.status == "SUCCESS"
    assert result.response == "done"


def test_registry_loader():
    registry = load_registry()
    assert "voice-assistant" in registry and "comfyui" in registry
    assert registry["voice-assistant"]["path"].startswith("/Users/")


def test_tool_availability_loader():
    availability = load_tool_availability()
    assert availability.get("codex") is True
    # 大王确认 agy CLI 免登录直接可用后启用为生产执行入口；
    # CLI 会话不进桌面列表的未达条件如实记录在验证报告
    assert availability.get("antigravity") is True


def test_build_turn_text_sends_requirement_once_then_only_new_followups():
    # 第一轮：只有原需求
    text, applied, req_done = build_turn_text("原需求", [], applied_followups=0, requirement_applied=False)
    assert text == "原需求" and applied == 0 and req_done is True
    # 第二轮：只发新增追加，绝不重放原需求或旧追加
    text, applied, req_done = build_turn_text(
        "原需求", ["补充一", "补充二"], applied_followups=1, requirement_applied=True
    )
    assert text == "补充二" and applied == 2 and req_done is True
    # 恢复场景：全部已消费 + 没有新增 → 空文本（不回放）
    text, applied, req_done = build_turn_text(
        "原需求", ["补充一"], applied_followups=1, requirement_applied=True
    )
    assert text == "" and applied == 1


def test_build_turn_text_resume_after_completion_only_new_followup():
    # 任务完成后被 followup 重新入队：requirement 已消费，只发新追加
    text, applied, _ = build_turn_text(
        "原需求", ["新追加"], applied_followups=0, requirement_applied=True
    )
    assert text == "新追加" and applied == 1


def test_server_event_forwards_followup_progress(monkeypatch):
    # 回归：applied_followups / requirement_applied 必须透传到事件端点
    from mac_executor.server_client import ServerClient

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})

    monkeypatch.setattr("mac_executor.server_client.httpx.post", fake_post)
    client = ServerClient(base_url="http://server", token="t")
    client.event(
        "task-1",
        applied_followups=2,
        requirement_applied=True,
    )
    assert captured["url"].endswith("/v1/project-tasks/task-1/events")
    assert captured["json"]["applied_followups"] == 2
    assert captured["json"]["requirement_applied"] is True
    client.event("task-1", status="completed", requirement_applied=False)
    assert captured["json"]["requirement_applied"] is False


# ---------------------------------------------------------------------------
# 事件确认语义与 resume 错误分类（监督要求的负例测试）
# ---------------------------------------------------------------------------

import time as _time

import mac_executor.daemon as daemon_mod
from mac_executor.daemon import TaskRunner


class FakeServer:
    """可编程失败的服务端替身：记录全部事件，event() 按 fail_times 计划失败。"""

    def __init__(self, fail_times=0, task=None):
        self.calls = []
        self.fail_remaining = fail_times
        self.task = task or {}

    def event(self, task_id, **kwargs):
        self.calls.append(kwargs)
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise RuntimeError("server unreachable (fake)")
        return {}

    def get_task(self, task_id):
        return dict(self.task)

    def claim(self, **kw):
        return None

    def heartbeat(self, **kw):
        return None


class FakeCodex:
    """记录调用的 codex 客户端替身；run_turn 可编程。"""

    instances = []

    def __init__(self, resume_error=None):
        self.resume_error = resume_error
        self.run_turn_calls = []
        self.resumed = []
        self.closed = False
        FakeCodex.instances.append(self)

    def start(self):
        pass

    def close(self):
        self.closed = True

    def resume_thread(self, thread_id):
        self.resumed.append(thread_id)
        if self.resume_error:
            raise CodexClientError(self.resume_error)

    def start_thread(self, cwd):
        return "new-thread-1"

    def run_turn(self, thread_id, text, **kw):
        self.run_turn_calls.append(text)
        return TurnResult(status="completed", final_text="fake done", turn_id="t1")


def _make_runner(monkeypatch, server, codex):
    from mac_executor.config import ExecutorConfig
    from pathlib import Path

    monkeypatch.setattr(daemon_mod, "CodexAppServerClient", lambda: codex)
    cfg = ExecutorConfig(
        server_url="http://server", api_token="t", executor_id="mac-test",
        registry_path=Path("/nonexistent"),
    )
    return TaskRunner(cfg, server, tool_availability={"codex": True})


def _task(**over):
    task = {"id": "task-abc", "project_id": "voice-assistant", "tool": "codex",
            "requirement": "原需求", "status": "running", "native_session_id": "th-1",
            "followups": [], "applied_followups": 0, "requirement_applied": False}
    task.update(over)
    return task


def test_progress_event_unconfirmed_never_starts_turn(monkeypatch):
    """负例：消费进度回传始终失败时，绝不能启动本轮执行（防重放已消费内容）。"""
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda s: None)  # 加速退避
    server = FakeServer(fail_times=99)  # 所有事件都失败
    codex = FakeCodex()
    runner = _make_runner(monkeypatch, server, codex)
    runner._run_codex_task(_task(), cwd="/tmp")
    assert codex.run_turn_calls == [], "进度未确认却执行了本轮"
    # 没有任何终态事件被发出（任务保持 running，由服务端失联清理如实呈现）
    assert not any(c.get("status") for c in server.calls)


def test_terminal_event_retries_until_confirmed(monkeypatch):
    """终态事件前两次失败后第三次成功：必须重试直至确认。"""
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda s: None)
    server = FakeServer(fail_times=2)
    codex = FakeCodex()
    runner = _make_runner(monkeypatch, server, codex)
    runner._run_codex_task(_task(), cwd="/tmp")
    statuses = [c.get("status") for c in server.calls if c.get("status")]
    assert statuses[-1] == "completed"
    assert len(server.calls) >= 3


def test_progress_confirmed_then_turn_runs(monkeypatch):
    """正常路径：进度事件确认后执行本轮，终态 completed。"""
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda s: None)
    server = FakeServer()
    codex = FakeCodex()
    runner = _make_runner(monkeypatch, server, codex)
    runner._run_codex_task(_task(), cwd="/tmp")
    assert codex.run_turn_calls == ["原需求"]
    statuses = [c.get("status") for c in server.calls if c.get("status")]
    assert statuses == ["completed"]
    progress = [c for c in server.calls if "requirement_applied" in c and "status" not in c]
    assert progress and progress[0]["requirement_applied"] is True


def test_resume_other_error_fails_with_real_reason(monkeypatch):
    """非 active writer 的 resume 错误必须按真实原因 failed，不得误报原工具占用。"""
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda s: None)
    server = FakeServer()
    codex = FakeCodex(resume_error='thread/resume: {"message": "thread not found"}')
    runner = _make_runner(monkeypatch, server, codex)
    runner._run_codex_task(_task(), cwd="/tmp")
    failed = [c for c in server.calls if c.get("status") == "failed"]
    assert failed, "非占用错误未标记失败"
    assert "thread not found" in failed[0]["error"]
    assert not any(c.get("status") == "waiting" for c in server.calls)


def test_resume_active_writer_exhausted_marks_waiting(monkeypatch):
    """active writer 重试耗尽 → waiting（真实占用语义）。"""
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda s: None)
    server = FakeServer()
    codex = FakeCodex(resume_error='thread/resume: thread X already has an active writer')
    runner = _make_runner(monkeypatch, server, codex)
    runner._run_codex_task(_task(), cwd="/tmp")
    waiting = [c for c in server.calls if c.get("status") == "waiting"]
    assert waiting and "原工具界面占用" in waiting[0]["note"]
    assert not any(c.get("status") == "failed" for c in server.calls)


def test_build_turn_text_sends_requirement_once_then_only_new_followups():
    # 第一轮：只有原需求
    text, applied, req_done = build_turn_text("原需求", [], applied_followups=0, requirement_applied=False)
    assert text == "原需求" and applied == 0 and req_done is True
    # 第二轮：只发新增追加，绝不重放原需求或旧追加
    text, applied, req_done = build_turn_text(
        "原需求", ["补充一", "补充二"], applied_followups=1, requirement_applied=True
    )
    assert text == "补充二" and applied == 2 and req_done is True
    # 恢复场景：全部已消费 + 没有新增 → 空文本（不回放）
    text, applied, req_done = build_turn_text(
        "原需求", ["补充一"], applied_followups=1, requirement_applied=True
    )
    assert text == "" and applied == 1


def test_build_turn_text_resume_after_completion_only_new_followup():
    # 任务完成后被 followup 重新入队：requirement 已消费，只发新追加
    text, applied, _ = build_turn_text(
        "原需求", ["新追加"], applied_followups=0, requirement_applied=True
    )
    assert text == "新追加" and applied == 1


def test_server_event_forwards_followup_progress(monkeypatch):
    # 回归：applied_followups / requirement_applied 必须透传到事件端点
    from mac_executor.server_client import ServerClient

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})

    monkeypatch.setattr("mac_executor.server_client.httpx.post", fake_post)
    client = ServerClient(base_url="http://server", token="t")
    client.event(
        "task-1",
        applied_followups=2,
        requirement_applied=True,
    )
    assert captured["url"].endswith("/v1/project-tasks/task-1/events")
    assert captured["json"]["applied_followups"] == 2
    assert captured["json"]["requirement_applied"] is True
    client.event("task-1", status="completed", requirement_applied=False)
    assert captured["json"]["requirement_applied"] is False


# ---------------------------------------------------------------------------
# 事件确认语义与 resume 错误分类（监督要求的负例测试）
# ---------------------------------------------------------------------------

import time as _time

import mac_executor.daemon as daemon_mod
from mac_executor.daemon import TaskRunner


class FakeServer:
    """可编程失败的服务端替身：记录全部事件，event() 按 fail_times 计划失败。"""

    def __init__(self, fail_times=0, task=None):
        self.calls = []
        self.fail_remaining = fail_times
        self.task = task or {}

    def event(self, task_id, **kwargs):
        self.calls.append(kwargs)
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise RuntimeError("server unreachable (fake)")
        return {}

    def get_task(self, task_id):
        return dict(self.task)

    def claim(self, **kw):
        return None

    def heartbeat(self, **kw):
        return None


class FakeCodex:
    """记录调用的 codex 客户端替身；run_turn 可编程。"""

    instances = []

    def __init__(self, resume_error=None):
        self.resume_error = resume_error
        self.run_turn_calls = []
        self.resumed = []
        self.closed = False
        FakeCodex.instances.append(self)

    def start(self):
        pass

    def close(self):
        self.closed = True

    def resume_thread(self, thread_id):
        self.resumed.append(thread_id)
        if self.resume_error:
            raise CodexClientError(self.resume_error)

    def start_thread(self, cwd):
        return "new-thread-1"

    def run_turn(self, thread_id, text, **kw):
        self.run_turn_calls.append(text)
        return TurnResult(status="completed", final_text="fake done", turn_id="t1")


def _make_runner(monkeypatch, server, codex):
    from mac_executor.config import ExecutorConfig
    from pathlib import Path

    monkeypatch.setattr(daemon_mod, "CodexAppServerClient", lambda: codex)
    cfg = ExecutorConfig(
        server_url="http://server", api_token="t", executor_id="mac-test",
        registry_path=Path("/nonexistent"),
    )
    return TaskRunner(cfg, server, tool_availability={"codex": True})


def _task(**over):
    task = {"id": "task-abc", "project_id": "voice-assistant", "tool": "codex",
            "requirement": "原需求", "status": "running", "native_session_id": "th-1",
            "followups": [], "applied_followups": 0, "requirement_applied": False}
    task.update(over)
    return task


def test_progress_event_unconfirmed_never_starts_turn(monkeypatch):
    """负例：消费进度回传始终失败时，绝不能启动本轮执行（防重放已消费内容）。"""
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda s: None)  # 加速退避
    server = FakeServer(fail_times=99)  # 所有事件都失败
    codex = FakeCodex()
    runner = _make_runner(monkeypatch, server, codex)
    runner._run_codex_task(_task(), cwd="/tmp")
    assert codex.run_turn_calls == [], "进度未确认却执行了本轮"
    # 没有任何终态事件被发出（任务保持 running，由服务端失联清理如实呈现）
    assert not any(c.get("status") for c in server.calls)


def test_terminal_event_retries_until_confirmed(monkeypatch):
    """终态事件前两次失败后第三次成功：必须重试直至确认。"""
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda s: None)
    server = FakeServer(fail_times=2)
    codex = FakeCodex()
    runner = _make_runner(monkeypatch, server, codex)
    runner._run_codex_task(_task(), cwd="/tmp")
    statuses = [c.get("status") for c in server.calls if c.get("status")]
    assert statuses[-1] == "completed"
    assert len(server.calls) >= 3


def test_progress_confirmed_then_turn_runs(monkeypatch):
    """正常路径：进度事件确认后执行本轮，终态 completed。"""
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda s: None)
    server = FakeServer()
    codex = FakeCodex()
    runner = _make_runner(monkeypatch, server, codex)
    runner._run_codex_task(_task(), cwd="/tmp")
    assert codex.run_turn_calls == ["原需求"]
    statuses = [c.get("status") for c in server.calls if c.get("status")]
    assert statuses == ["completed"]
    progress = [c for c in server.calls if "requirement_applied" in c and "status" not in c]
    assert progress and progress[0]["requirement_applied"] is True


def test_resume_other_error_fails_with_real_reason(monkeypatch):
    """非 active writer 的 resume 错误必须按真实原因 failed，不得误报原工具占用。"""
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda s: None)
    server = FakeServer()
    codex = FakeCodex(resume_error='thread/resume: {"message": "thread not found"}')
    runner = _make_runner(monkeypatch, server, codex)
    runner._run_codex_task(_task(), cwd="/tmp")
    failed = [c for c in server.calls if c.get("status") == "failed"]
    assert failed, "非占用错误未标记失败"
    assert "thread not found" in failed[0]["error"]
    assert not any(c.get("status") == "waiting" for c in server.calls)


def test_resume_active_writer_exhausted_marks_waiting(monkeypatch):
    """active writer 重试耗尽 → waiting（真实占用语义）。"""
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda s: None)
    server = FakeServer()
    codex = FakeCodex(resume_error='thread/resume: thread X already has an active writer')
    runner = _make_runner(monkeypatch, server, codex)
    runner._run_codex_task(_task(), cwd="/tmp")
    waiting = [c for c in server.calls if c.get("status") == "waiting"]
    assert waiting and "原工具界面占用" in waiting[0]["note"]
    assert not any(c.get("status") == "failed" for c in server.calls)


