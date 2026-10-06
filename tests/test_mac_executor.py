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


def test_agy_json_parsing(monkeypatch):
    payload = json.dumps({
        "conversation_id": "conv-123",
        "status": "SUCCESS",
        "response": "ok",
        "denied_actions": [{"action": "command", "display_name": "RunCommand"}],
    })
    captured = {}

    def fake_run(argv, capture_output, text, timeout):
        captured["argv"] = argv
        return SimpleNamespace(stdout=payload + "\n", stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = agy_client.run_agy_turn("只读检查", project="voice-assistant")
    assert result.status == "SUCCESS"
    assert result.conversation_id == "conv-123"
    assert result.denied_actions == ["RunCommand"]
    assert "--conversation" not in captured["argv"]

    result2 = agy_client.run_agy_turn("继续", project="voice-assistant", conversation_id="conv-123")
    idx = captured["argv"].index("--conversation")
    assert captured["argv"][idx + 1] == "conv-123"
    assert result2.response == "ok"


def test_agy_no_json_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="plain text", stderr="boom", returncode=1))
    result = agy_client.run_agy_turn("x", project="p")
    assert result.status == "ERROR" and "boom" in (result.raw_error or "")


def test_registry_loader():
    registry = load_registry()
    assert "voice-assistant" in registry and "comfyui" in registry
    assert registry["voice-assistant"]["path"].startswith("/Users/")


def test_tool_availability_loader():
    availability = load_tool_availability()
    assert availability.get("codex") is True
    assert availability.get("antigravity") is False  # 未通过原工具列表验收


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
