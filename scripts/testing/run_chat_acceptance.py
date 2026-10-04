"""Run the designed /v1/chat suite inside disposable Docker services.

Real classification, completions, Harness and Memos; only history persistence
is redirected locally. Fault injection is limited to explicitly marked cases.
Outputs raw evidence, leaving semantic judgment to report review.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai.resources.chat.completions import AsyncCompletions

from press_to_talk.api import fast_chat
from press_to_talk.api import main as api
from press_to_talk.harness.client import DeepSeekHarnessClient
from press_to_talk.storage.providers.memos import MemosClient

ROOT = Path(os.environ.get("ACCEPTANCE_ROOT", "/test"))
SUITE = json.loads((ROOT / "chat_acceptance.json").read_text())
CURRENT: dict = {}
HISTORY: list[dict] = []


def event(kind, **data):
    CURRENT.setdefault("events", []).append({"kind": kind, "t": round(time.monotonic(), 4), **data})


def instrument():
    original_decide = fast_chat.ask_intent_and_delivery

    def decide(*args, **kwargs):
        result = original_decide(*args, **kwargs)
        event("decision", result=result)
        return result

    fast_chat.ask_intent_and_delivery = decide
    original_complete = AsyncCompletions.create

    async def complete(self, *args, **kwargs):
        summary = kwargs.get("messages", [{}])[0].get("content") == fast_chat._prompt("memo_record_summary")
        kind = "summary" if summary else "completion"
        event(kind + "_start", model=kwargs.get("model"))
        fault = CURRENT.get("fault_id")
        if summary and fault == "F01":
            raise httpx.ReadTimeout("injected completion timeout")
        if summary and fault in ("F02", "F03"):
            text = ("" if CURRENT.get("repeat") == 1 else "   ") if fault == "F02" else "护照在书房"
            response = SimpleNamespace(choices=[SimpleNamespace(
                finish_reason="stop" if fault == "F02" else "length",
                message=SimpleNamespace(content=text),
            )])
        else:
            response = await original_complete(self, *args, **kwargs)
        event(kind + "_end", choices=[{
            "finish_reason": c.finish_reason, "content": c.message.content,
        } for c in response.choices])
        return response

    patch.object(AsyncCompletions, "create", complete).start()
    for method in ("create_memo", "list_memos", "update_memo", "delete_memo"):
        original = getattr(MemosClient, method)

        def wrapper(self, *args, _method=method, _original=original, **kwargs):
            if CURRENT.get("tracking"):
                event("memos_" + _method, content=args[0] if args and _method == "create_memo" else None,
                      filter=kwargs.get("filter_expr"))
                if _method == "create_memo" and CURRENT.get("fault_id") == "F04":
                    raise httpx.HTTPStatusError("injected Memos rejection 403",
                        request=httpx.Request("POST", "http://test-memos:5230/api/v1/memos"),
                        response=httpx.Response(403))
            result = _original(self, *args, **kwargs)
            if CURRENT.get("tracking") and _method == "create_memo":
                event("memos_created", name=result.get("name"))
            return result

        setattr(MemosClient, method, wrapper)
    original_history = DeepSeekHarnessClient._history

    async def harness_history(self, session_id):
        entries = await original_history(self, session_id)
        captured = []
        for entry in entries:
            e = entry.get("event", {})
            data = e.get("data") or {}
            if e.get("type") in ("tool/call", "tool/result", "turn/end", "turn/error"):
                captured.append({"seq": e.get("seq"), "type": e.get("type"), "data": data})
            if e.get("type") == "assistant/message":
                blocks = (data.get("message") or {}).get("content") or []
                tools = [b for b in blocks if isinstance(b, dict) and b.get("type") in ("tool-call", "tool_call")]
                if tools:
                    captured.append({"seq": e.get("seq"), "type": "assistant/tool-call", "tools": tools})
        CURRENT.setdefault("harness_sessions", {})[session_id] = captured
        return entries

    DeepSeekHarnessClient._history = harness_history
    api._persist_harness_history = lambda **kwargs: HISTORY.append(kwargs)


def bootstrap_memos():
    url = os.environ["MEMOS_BASE_URL"]
    assert url == "http://test-memos:5230", "refusing non-disposable Memos target"
    password = secrets.token_urlsafe(24)
    with httpx.Client(timeout=10) as c:
        response = c.post(url + "/api/v1/users", json={
            "username": "acceptance", "password": password, "role": "ADMIN", "state": "NORMAL",
        })
        response.raise_for_status()
        response = c.post(url + "/api/v1/auth/signin", json={
            "passwordCredentials": {"username": "acceptance", "password": password},
        })
        response.raise_for_status()
        token = response.json()["accessToken"]
    os.environ["MEMOS_TOKEN"] = token
    return MemosClient(base_url=url, token=token, timeout=5)


def reset(memos, fixtures):
    CURRENT["tracking"] = False
    for memo in memos.list_memos(page_size=100).get("memos", []):
        memos.delete_memo(memo["name"])
    for f in SUITE["fixtures"]:
        if f["id"] in fixtures:
            memos.create_memo(f["content"], visibility="PRIVATE")
    return {m["name"]: m for m in memos.list_memos(page_size=100).get("memos", [])}


def structural_checks(case, result):
    e = case["expected"]
    data = result.get("response", {})
    decisions = [ev["result"] for ev in result["events"] if ev["kind"] == "decision"]
    intent = (decisions[0] or {}).get("intent") if decisions else None
    allowed = e["intent"] if isinstance(e["intent"], list) else [e["intent"]]
    checks = {"http_200": result["http_status"] == 200,
              "classification": intent in allowed,
              "action": data.get("action") == e["action"],
              "writes": len(result["new_memos"]) == (1 if intent == "record" else 0)
                        if case["id"] == "O01" else len(result["new_memos"]) == e["memo_writes"],
              "fixtures_unchanged": not result["changed_fixtures"]}
    calls = [ev["kind"] for ev in result["events"]]
    if e["intent"] == "chat":
        checks["no_memos_calls"] = not any(x.startswith("memos_") for x in calls)
    if e["intent"] == "query":
        checks["single_query"] = calls.count("memos_list_memos") == 1
    if result["new_memos"]:
        checks["summary_before_write"] = "summary_end" in calls and "memos_create_memo" in calls and calls.index("summary_end") < calls.index("memos_create_memo")
        checks["private_with_original"] = all(
            m.get("visibility") == "PRIVATE" and case["request"]["query"] in m.get("content", "") and "#voice" in m.get("content", "")
            for m in result["new_memos"])
        checks["reply_matches_memo"] = all(data.get("reply") == "✅ 已记入 Memos：" + m.get("content", "").split("\n\n> 语音原文:")[0].split("\n\n#voice")[0] for m in result["new_memos"])
    if case.get("fault"):
        checks["failure_stage"] = data.get("debug_info", {}).get("stage") == ("record_memo" if case["id"] == "F04" else "record_summary")
        checks["no_false_success"] = "记录失败" in data.get("reply", "")
    return checks


async def run():
    os.environ["PTT_CURRENT_TIME"] = SUITE["fixed_current_time"]
    token_file = ROOT / "harness-token"
    if token_file.exists():
        os.environ["PTT_HARNESS_API_TOKEN"] = token_file.read_text().strip()
    memos = bootstrap_memos()
    instrument()
    output = ROOT / "results.jsonl"
    metadata = {"suite": SUITE["name"], "repeats": 3, "fixed_current_time": SUITE["fixed_current_time"],
                "source_sha256": hashlib.sha256(Path("/app/press_to_talk/api/fast_chat.py").read_bytes()).hexdigest(),
                "execution": "FastAPI /v1/chat via ASGITransport; real Clef/ChatCompletion/Harness/Memos; isolated history sink; faults injected only for F01-F04",
                "memos_version": "0.30.0", "history_sink": "in-process test list"}
    (ROOT / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    count = len(output.read_text().splitlines()) if output.exists() else 0
    start_case = os.environ.get("ACCEPTANCE_START_CASE")
    started = start_case is None
    try:
        async with (
            api.lifespan(api.app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://acceptance") as client,
        ):
            for case in SUITE["cases"]:
                if not started:
                    started = case["id"] == start_case
                    if not started:
                        continue
                for repeat in range(1, 4):
                    CURRENT.clear()
                    before = reset(memos, case.get("fixtures", []))
                    CURRENT.update(tracking=True, fault_id=case["id"] if case.get("fault") else None, repeat=repeat, events=[])
                    start = time.monotonic()
                    response = await client.post("/v1/chat", json=case["request"], headers={"Authorization": "Bearer " + os.environ["PTT_API_KEY"]})
                    elapsed = time.monotonic() - start
                    CURRENT["tracking"] = False
                    after = {m["name"]: memos.get_memo(m["name"]) for m in memos.list_memos(page_size=100).get("memos", [])}
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {"raw": response.text[:2000]}
                    result = {"id": case["id"], "repeat": repeat, "elapsed_s": round(elapsed, 3),
                              "http_status": response.status_code, "response": payload, "events": CURRENT["events"],
                              "harness_sessions": CURRENT.get("harness_sessions", {}),
                              "new_memos": [m for n, m in after.items() if n not in before],
                              "changed_fixtures": [n for n, m in before.items() if n not in after or m["content"] != after[n]["content"]]}
                    result["structural_checks"] = structural_checks(case, result)
                    with output.open("a") as f:
                        f.write(json.dumps(result, ensure_ascii=False) + "\n")
                    count += 1
                    print(json.dumps({"progress": f"{count}/120", "id": case["id"], "repeat": repeat,
                        "elapsed_s": result["elapsed_s"], "http": response.status_code,
                        "structural_failures": [k for k, v in result["structural_checks"].items() if not v]}, ensure_ascii=False), flush=True)
    finally:
        reset(memos, [])
        (ROOT / "cleanup.json").write_text(json.dumps({"remaining_test_memos": len(memos.list_memos(page_size=100).get("memos", [])), "history_count": len(HISTORY)}))
        print("CLEANUP: disposable Memos empty", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
