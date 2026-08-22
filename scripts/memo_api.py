#!/usr/bin/env python3
"""Minimal command-line access to the Mem0 scope used by the memo agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request


BASE_URL = "https://api.mem0.ai"
USER_ID = "soj"
DEFAULT_TIMEOUT_SECONDS = 7.0


def _token() -> str:
    token = os.environ.get("MEM0_API_KEY") or os.environ.get("MEM0_MCP_TOKEN") or ""
    if not token:
        token = _token_from_dsh_env()
    if not token:
        raise SystemExit("MEM0_API_KEY or MEM0_MCP_TOKEN is required")
    return token


def _token_from_dsh_env() -> str:
    """Read only the two supported keys from the Harness-managed .env file.

    Harness deliberately scrubs ambient credential-shaped variables before it
    spawns model shell commands. Keeping the fallback file-local avoids putting
    the Mem0 token back into every child process environment.
    """
    dsh_home = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    path = dsh_home / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""

    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        if separator == "":
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value

    return values.get("MEM0_API_KEY") or values.get("MEM0_MCP_TOKEN") or ""


def _request_timeout_seconds() -> float:
    raw = os.environ.get("MEM0_REQUEST_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(1.0, min(float(raw), 30.0))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _request(
    path: str,
    payload: dict[str, object] | None = None,
    *,
    method: str = "POST",
    query: dict[str, int] | None = None,
) -> object:
    token = _token()
    url = BASE_URL + path
    if query:
        from urllib.parse import urlencode

        url += "?" + urlencode(query)
    request = urllib.request.Request(
        url,
        data=(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        ),
        method=method,
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
            # Mem0's client derives this value from the token; it scopes API access.
            "Mem0-User-ID": hashlib.md5(token.encode()).hexdigest(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_request_timeout_seconds()) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Mem0 HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(f"Mem0 request failed: {exc}") from exc


def _memory_items(payload: object) -> list[dict[str, Any]]:
    raw_items: object
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        raw_items = payload.get("results", [])
    else:
        raw_items = []

    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _compact_item(item: dict[str, Any]) -> dict[str, object]:
    compact: dict[str, object] = {}
    for key in ("id", "memory", "score", "created_at"):
        value = item.get(key)
        if value is not None:
            compact[key] = value
    return compact


def _compact_result(payload: object) -> dict[str, object]:
    items = [_compact_item(item) for item in _memory_items(payload)]
    result: dict[str, object] = {"results": items}
    if isinstance(payload, dict):
        for key in ("count", "next", "previous"):
            value = payload.get(key)
            if value is not None:
                result[key] = value
    return result


def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add")
    add.add_argument("--text", required=True, help="original user text")

    search = commands.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=20)

    listed = commands.add_parser("list")
    listed.add_argument("--page", type=int, default=1)
    listed.add_argument("--page-size", type=int, default=100)

    delete = commands.add_parser("delete")
    delete.add_argument("--id", required=True, dest="memory_id")

    args = parser.parse_args(argv)
    if args.command == "add":
        payload = {
            "messages": [{"role": "user", "content": args.text}],
            "user_id": USER_ID,
            "infer": False,
            "async_mode": False,
            "output_format": "v1.1",
        }
        result = {
            "reply": "已记录。",
            **_compact_result(_request("/v1/memories/", payload)),
        }
    elif args.command == "search":
        result = _compact_result(
            _request(
                "/v2/memories/search/",
                {
                    "query": args.query,
                    "filters": {"AND": [{"user_id": USER_ID}]},
                    "top_k": max(1, min(args.limit, 20)),
                    "rerank": False,
                },
            )
        )
    elif args.command == "list":
        result = _compact_result(
            _request(
                "/v2/memories/",
                {"filters": {"AND": [{"user_id": USER_ID}]}},
                query={
                    "page": max(1, args.page),
                    "page_size": max(1, min(args.page_size, 100)),
                },
            )
        )
    else:
        memory_id = args.memory_id.strip()
        if not memory_id:
            raise SystemExit("memory id is required")
        response = _request(
            f"/v1/memories/{memory_id}/",
            method="DELETE",
        )
        result = {
            "reply": "已删除。",
            "deleted": memory_id,
            "message": (
                response.get("message", "Memory deleted successfully")
                if isinstance(response, dict)
                else "Memory deleted successfully"
            ),
        }

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    _main()
