import asyncio
import base64
import json

import httpx

from press_to_talk.harness import DeepSeekHarnessClient, HarnessError


def _response(rpc_id: str, value: dict | None = None, error: dict | None = None) -> httpx.Response:
    result = {"ok": True, "value": value or {}}
    if error is not None:
        result = {"ok": False, "error": error}
    return httpx.Response(
        200,
        json={"type": "server-response", "rpcId": rpc_id, "result": result},
    )


def test_harness_client_creates_prompts_and_reads_final_history() -> None:
    requests: list[httpx.Request] = []
    history_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal history_calls
        requests.append(request)
        body = json.loads(request.content)
        method = body["method"]
        rpc_id = body["rpcId"]
        if method == "session.create":
            return _response(rpc_id, {"sessionId": "session-1"})
        if method == "session.history":
            history_calls += 1
            if history_calls == 1:
                return _response(rpc_id, {"events": [], "hasMore": False})
            if history_calls == 2:
                return _response(rpc_id, {
                    "events": [{
                        "event": {
                            "seq": 1,
                            "type": "assistant/message",
                            "time": 1,
                            "data": {"message": {"role": "assistant", "content": []}},
                        },
                    }],
                    "hasMore": False,
                })
            return _response(rpc_id, {
                "events": [{
                    "event": {
                        "seq": 1,
                        "type": "assistant/message",
                        "time": 1,
                        "data": {
                            "message": {
                                "role": "assistant",
                                "content": [{"type": "text", "text": "护照在白柜子里。"}],
                            },
                        },
                    },
                }],
                "hasMore": False,
            })
        if method == "session.prompt":
            return _response(rpc_id, {"accepted": True})
        raise AssertionError(method)

    async def run() -> dict:
        client = DeepSeekHarnessClient(
            "http://harness.test",
            transport=httpx.MockTransport(handler),
            poll_interval_seconds=0.01,
        )
        try:
            return await client.query(
                "我的护照在哪里",
                photo={"type": "base64", "mime": "image/png", "data": base64.b64encode(b"x").decode()},
            )
        finally:
            await client.close()

    result = asyncio.run(run())

    assert result["reply"] == "护照在白柜子里。"
    assert result["memories"] == []
    assert [request.url.path for request in requests] == [
        "/api/session.create",
        "/api/session.history",
        "/api/session.prompt",
        "/api/session.history",
        "/api/session.history",
    ]
    prompt_body = json.loads(requests[2].content)
    assert prompt_body["payload"]["content"][0] == {"type": "text", "text": "我的护照在哪里"}
    assert prompt_body["payload"]["content"][1]["mediaType"] == "image/png"
    assert prompt_body["payload"]["content"][1]["data"] == base64.b64encode(b"x").decode()


def test_harness_client_surfaces_business_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return _response(
            body["rpcId"],
            error={"code": "agent-preset-not-found", "message": "preset missing", "details": {}},
        )

    async def run() -> None:
        client = DeepSeekHarnessClient("http://harness.test", transport=httpx.MockTransport(handler))
        try:
            await client.query("测试")
        finally:
            await client.close()

    try:
        asyncio.run(run())
    except HarnessError as exc:
        assert "agent-preset-not-found" in str(exc)
    else:
        raise AssertionError("expected HarnessError")
