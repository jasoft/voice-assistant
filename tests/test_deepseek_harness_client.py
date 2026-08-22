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
            timeout_seconds=1,
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


def test_harness_client_surfaces_terminal_turn_errors_without_waiting_for_timeout() -> None:
    history_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal history_calls
        body = json.loads(request.content)
        method = body["method"]
        rpc_id = body["rpcId"]
        if method == "session.create":
            return _response(rpc_id, {"sessionId": "session-error"})
        if method == "session.history":
            history_calls += 1
            if history_calls == 1:
                return _response(rpc_id, {"events": [], "hasMore": False})
            return _response(rpc_id, {
                "events": [{
                    "event": {
                        "seq": 1,
                        "type": "turn/end",
                        "data": {
                            "reason": {
                                "kind": "error",
                                "error": {"message": "Connection error."},
                            },
                        },
                    },
                }],
                "hasMore": False,
            })
        if method == "session.prompt":
            return _response(rpc_id, {"accepted": True})
        raise AssertionError(method)

    async def run() -> None:
        client = DeepSeekHarnessClient(
            "http://harness.test",
            transport=httpx.MockTransport(handler),
            poll_interval_seconds=0.01,
            timeout_seconds=60,
        )
        try:
            await client.query("测试")
        finally:
            await client.close()

    try:
        asyncio.run(run())
    except HarnessError as exc:
        assert str(exc) == "DeepSeek Harness Agent 执行失败：Connection error."
    else:
        raise AssertionError("expected HarnessError")


def test_harness_client_returns_reply_from_a_concluding_tool_result() -> None:
    history_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal history_calls
        body = json.loads(request.content)
        method = body["method"]
        rpc_id = body["rpcId"]
        if method == "session.create":
            return _response(rpc_id, {"sessionId": "session-tool-reply"})
        if method == "session.history":
            history_calls += 1
            if history_calls == 1:
                return _response(rpc_id, {"events": [], "hasMore": False})
            return _response(rpc_id, {
                "events": [
                    {
                        "event": {
                            "seq": 1,
                            "type": "assistant/message",
                            "data": {
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "tool-call", "name": "bash"}],
                                },
                            },
                        },
                    },
                    {
                        "event": {
                            "seq": 2,
                            "type": "tool/result",
                            "data": {
                                "message": {
                                    "role": "toolResult",
                                    "isError": False,
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": '{"reply":"已记录。","results":[]}\\n[exit code: 0]',
                                        }
                                    ],
                                },
                            },
                        },
                    },
                    {
                        "event": {
                            "seq": 3,
                            "type": "turn/end",
                            "data": {"reason": {"kind": "completed"}},
                        },
                    },
                ],
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
            return await client.query("记住测试内容")
        finally:
            await client.close()

    assert asyncio.run(run())["reply"] == "已记录。"


def test_harness_client_lists_completed_history_turns() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "session.create":
            return _response(body["rpcId"], {"sessionId": "session-history"})
        if method == "session.history":
            return _response(body["rpcId"], {"events": [
                {"event": {"seq": 2, "time": "2026-08-20T10:00:00+08:00", "type": "user/message", "data": {
                    "message": {"role": "user", "content": [{"type": "text", "text": "我把钥匙放哪里了"}]},
                }}},
                {"event": {"seq": 3, "time": "2026-08-20T10:00:01+08:00", "type": "assistant/message", "data": {
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "钥匙在抽屉里。"}]},
                }}},
            ]})
        raise AssertionError(method)

    async def run() -> list[dict[str, str]]:
        client = DeepSeekHarnessClient("http://harness.test", transport=httpx.MockTransport(handler))
        try:
            return await client.list_history()
        finally:
            await client.close()

    assert asyncio.run(run()) == [{
        "session_id": "session-history:3",
        "transcript": "我把钥匙放哪里了",
        "reply": "钥匙在抽屉里。",
        "created_at": "2026-08-20T10:00:00+08:00",
    }]
