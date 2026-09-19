#!/usr/bin/env python3
"""Minimal command-line access to Memos for voice assistant and harness agents."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "http://ds.home:5230"
DEFAULT_API_KEY = "memos_pat_voice_assistant_lan_2026"
DEFAULT_TIMEOUT_SECONDS = 7.0


def _dsh_env_values() -> dict[str, str]:
    """Read configuration from the Harness-managed .env file."""
    dsh_home = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    path = dsh_home / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

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

    return values


def _base_url() -> str:
    env_url = (
        os.environ.get("MEMOS_BASE_URL")
        or os.environ.get("MEMOS_API_URL")
        or ""
    )
    if not env_url:
        dsh_values = _dsh_env_values()
        env_url = (
            dsh_values.get("MEMOS_BASE_URL")
            or dsh_values.get("MEMOS_API_URL")
            or ""
        )
    return (env_url or DEFAULT_BASE_URL).rstrip("/")


def _token() -> str:
    token = (
        os.environ.get("MEMOS_TOKEN")
        or os.environ.get("MEMOS_ACCESS_TOKEN")
        or ""
    )
    if not token:
        dsh_values = _dsh_env_values()
        token = (
            dsh_values.get("MEMOS_TOKEN")
            or dsh_values.get("MEMOS_ACCESS_TOKEN")
            or ""
        )
    return token or DEFAULT_API_KEY


def _request_timeout_seconds() -> float:
    raw = os.environ.get("MEMOS_REQUEST_TIMEOUT_SECONDS", "").strip()
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
    query: dict[str, Any] | None = None,
) -> Any:
    token = _token()
    url = _base_url() + path
    if query:
        url += "?" + urllib.parse.urlencode(query)

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=_request_timeout_seconds()) as response:
            content = response.read().decode("utf-8")
            if not content.strip():
                return {}
            return json.loads(content)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Memos HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(f"Memos request failed: {exc}") from exc


def _clean_memory(content: str) -> str:
    """Strip tags and voice prefixes to provide clean memory text."""
    lines = content.strip().splitlines()
    cleaned_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("# "):
            continue
        if stripped.startswith("> 原话：") or stripped.startswith("> 语音原文:"):
            continue
        cleaned_lines.append(line)
    result = "\n".join(cleaned_lines).strip()
    return result or content.strip()


def _format_memo_item(memo: dict[str, Any]) -> dict[str, Any]:
    content = str(memo.get("content", ""))
    return {
        "id": memo.get("name", ""),
        "memory": _clean_memory(content),
        "created_at": memo.get("createTime", ""),
        "updated_at": memo.get("updateTime", ""),
    }


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
        text = args.text.strip()
        payload = {
            "content": f"{text}\n\n#voice",
            "visibility": "PRIVATE",
        }
        res = _request("/api/v1/memos", payload, method="POST")
        memo_id = res.get("name", "") if isinstance(res, dict) else ""
        result = {
            "reply": "已记录到 Memos。",
            "results": [{"id": memo_id, "memory": text}],
        }
    elif args.command == "search":
        query_text = args.query.strip()
        matched: list[dict[str, Any]] = []

        # Try CEL contains query if query doesn't have quotes/special chars
        if query_text and not any(c in query_text for c in ("'", '"', "\\")):
            try:
                cel = f"content.contains('{query_text}')"
                res = _request("/api/v1/memos", method="GET", query={"filter": cel, "pageSize": 50})
                if isinstance(res, dict):
                    matched = res.get("memos", [])
            except Exception:
                pass

        # Fallback to listing and local match
        if not matched:
            try:
                res = _request("/api/v1/memos", method="GET", query={"pageSize": 100})
                memos = res.get("memos", []) if isinstance(res, dict) else []
                tokens = [t.lower() for t in query_text.split() if t.strip()]
                for memo in memos:
                    content = str(memo.get("content", "")).lower()
                    if not tokens or all(token in content for token in tokens):
                        matched.append(memo)
            except Exception:
                matched = []

        items = [_format_memo_item(m) for m in matched[: args.limit]]
        result = {
            "results": items,
            "count": len(items),
        }
    elif args.command == "list":
        res = _request(
            "/api/v1/memos",
            method="GET",
            query={"pageSize": max(1, min(args.page_size, 100))},
        )
        memos = res.get("memos", []) if isinstance(res, dict) else []
        items = [_format_memo_item(m) for m in memos]
        result = {
            "results": items,
            "count": len(items),
        }
    else:
        memory_id = args.memory_id.strip()
        if not memory_id:
            raise SystemExit("memory id is required")
        path_name = memory_id if memory_id.startswith("memos/") else f"memos/{memory_id}"
        _request(f"/api/v1/{path_name}", method="DELETE")
        result = {
            "reply": "已删除。",
            "deleted": memory_id,
            "message": "Memory deleted successfully",
        }

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    _main()
