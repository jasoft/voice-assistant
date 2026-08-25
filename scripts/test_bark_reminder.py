#!/usr/bin/env python3
"""Run a real Bark + QStash reminder acceptance check without printing credentials."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REQUIRED_KEYS = ("QSTASH_TOKEN", "BARK_URL")


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def encode_path(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def request_json(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    request = urllib.request.Request(url, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if body is not None:
        request.add_header("Content-Type", "text/plain; charset=utf-8")
        request.data = body.encode("utf-8")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as error:
        payload = error.read().decode("utf-8", "replace")
        status = error.code
    try:
        return status, json.loads(payload)
    except json.JSONDecodeError:
        return status, payload


def bark_url(base: str, message: str, group: str) -> str:
    return f"{base.rstrip('/')}/{encode_path(message)}?group={urllib.parse.quote(group, safe='')}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one direct Bark push and one delayed QStash reminder.")
    parser.add_argument("--message", default="【验收测试】Mac GUI 提醒链路")
    parser.add_argument("--group", default="Mac提醒")
    parser.add_argument("--delay", type=int, default=65, help="QStash delay in seconds")
    parser.add_argument("--no-wait", action="store_true", help="Do not wait and inspect the scheduled message")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()

    env = load_dotenv(args.env_file.resolve())
    missing = [key for key in REQUIRED_KEYS if not env.get(key)]
    if missing:
        print(f"missing_config={','.join(missing)}")
        return 2

    qstash_base = (env.get("QSTASH_URL") or "https://qstash.upstash.io").rstrip("/")
    bark_base = env["BARK_URL"].rstrip("/")
    token = env["QSTASH_TOKEN"]
    direct_url = bark_url(bark_base, f"{args.message}（直连）", args.group)
    direct_status, direct_payload = request_json(direct_url)
    direct_ok = direct_status == 200 and isinstance(direct_payload, dict) and direct_payload.get("code") == 200
    print(f"direct_bark_http_status={direct_status} ok={str(direct_ok).lower()}")

    when = int(time.time() + args.delay)
    destination = bark_url(bark_base, f"{args.message}（QStash 定时）", args.group)
    publish_url = f"{qstash_base}/v2/publish/{destination}"
    schedule_status, schedule_payload = request_json(
        publish_url,
        method="POST",
        token=token,
        headers={
            "Upstash-Not-Before": str(when),
            "Upstash-Method": "GET",
        },
    )
    message_id = ""
    if isinstance(schedule_payload, dict):
        message_id = str(schedule_payload.get("messageId") or schedule_payload.get("message_id") or "")
    schedule_ok = schedule_status in (200, 201) and bool(message_id)
    scheduled_clock = time.strftime("%H:%M:%S", time.localtime(when))
    print(
        f"qstash_schedule_http_status={schedule_status} ok={str(schedule_ok).lower()} "
        f"scheduled_time={scheduled_clock} captured_message_id={str(bool(message_id)).lower()}"
    )
    if not schedule_ok or args.no_wait:
        return 0 if direct_ok and schedule_ok else 1

    time.sleep(max(0, args.delay + 8))
    lookup_status, lookup_payload = request_json(f"{qstash_base}/v2/messages/{message_id}", token=token)
    # Upstash removes completed messages from the active-message endpoint. A 404
    # after the due time therefore means it is no longer pending, while 200 lets
    # us expose its current state.
    if lookup_status == 200:
        state = lookup_payload.get("state") or lookup_payload.get("status") if isinstance(lookup_payload, dict) else None
        print(f"qstash_message_lookup_http_status=200 state={state}")
    elif lookup_status == 404:
        print("qstash_message_lookup_http_status=404 pending_message_removed=true")
    else:
        print(f"qstash_message_lookup_http_status={lookup_status}")
    return 0 if direct_ok and schedule_ok else 1


if __name__ == "__main__":
    sys.exit(main())
