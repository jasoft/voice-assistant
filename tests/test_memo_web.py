from __future__ import annotations

from fastapi.testclient import TestClient

from press_to_talk import memo_web
from press_to_talk.api import fast_chat


class FakeHarnessClient:
    agent_preset = "memo-mem0"

    def __init__(self) -> None:
        self.instructions: list[str] = []
        self.closed = False

    async def query(self, instruction: str) -> dict:
        self.instructions.append(instruction)
        return {
            "reply": "护照在书房白柜子的第一个抽屉里。",
            "debug_info": {"session_id": "memo-session-1"},
        }

    async def close(self) -> None:
        self.closed = True


def _disable_fast_path(monkeypatch) -> None:
    """让 fast-path 不命中，强制走 Harness 回退，隔离外部依赖。"""

    async def _none(_query: str):
        return None

    monkeypatch.setattr(fast_chat, "try_fast_memory_chat", _none)


def test_memo_web_sends_instruction_and_displays_final_reply(monkeypatch) -> None:
    fake_client = FakeHarnessClient()
    monkeypatch.setattr(memo_web.DeepSeekHarnessClient, "from_env", lambda: fake_client)
    _disable_fast_path(monkeypatch)

    with TestClient(memo_web.app) as client:
        response = client.post("/api/query", json={"instruction": "我的护照在哪里？"})

    assert response.status_code == 200
    assert response.json() == {
        "reply": "护照在书房白柜子的第一个抽屉里。",
        "agent": "memo-mem0",
        "session_id": "memo-session-1",
    }
    assert fake_client.instructions == ["我的护照在哪里？"]
    assert fake_client.closed is True


def test_memo_web_rejects_blank_instruction(monkeypatch) -> None:
    fake_client = FakeHarnessClient()
    monkeypatch.setattr(memo_web.DeepSeekHarnessClient, "from_env", lambda: fake_client)

    with TestClient(memo_web.app) as client:
        response = client.post("/api/query", json={"instruction": "   "})

    assert response.status_code == 422
    assert "指令不能为空" in response.json()["detail"]


def test_memo_web_serves_mobile_shell(monkeypatch) -> None:
    fake_client = FakeHarnessClient()
    monkeypatch.setattr(memo_web.DeepSeekHarnessClient, "from_env", lambda: fake_client)

    with TestClient(memo_web.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Memo Agent" in response.text


def test_memo_web_hides_memo_ids(monkeypatch) -> None:
    class FakeHarnessWithIds:
        agent_preset = "memo-mem0"
        async def query(self, instruction: str) -> dict:
            return {
                "reply": "找到记录：\n- 护照在书房抽屉里 (memos/Zd8VQWwWqvnNXWX3BDYapD)\n- 身份证在钱包里 (ID: memos/MBvA6rmWupfKmLy6bf8kom)",
                "debug_info": {"session_id": "memo-session-id-clean"},
            }
        async def close(self) -> None:
            pass

    monkeypatch.setattr(memo_web.DeepSeekHarnessClient, "from_env", lambda: FakeHarnessWithIds())
    _disable_fast_path(monkeypatch)

    with TestClient(memo_web.app) as client:
        response = client.post("/api/query", json={"instruction": "我的证件在哪里？"})

    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "memos/" not in reply
    assert "护照在书房抽屉里" in reply
    assert "身份证在钱包里" in reply


def test_memo_web_serves_version(monkeypatch) -> None:
    fake_client = FakeHarnessClient()
    monkeypatch.setattr(memo_web.DeepSeekHarnessClient, "from_env", lambda: fake_client)

    with TestClient(memo_web.app) as client:
        response = client.get("/api/version")

    assert response.status_code == 200
    assert "version" in response.json()
    assert response.json()["version"] != ""

