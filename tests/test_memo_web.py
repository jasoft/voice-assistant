from __future__ import annotations

from fastapi.testclient import TestClient

from press_to_talk import memo_web


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


def test_memo_web_sends_instruction_and_displays_final_reply(monkeypatch) -> None:
    fake_client = FakeHarnessClient()
    monkeypatch.setattr(memo_web.DeepSeekHarnessClient, "from_env", lambda: fake_client)

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
