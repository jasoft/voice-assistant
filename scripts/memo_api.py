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
DEFAULT_TIMEOUT_SECONDS = 15.0


class MemosRequestError(RuntimeError):
    """Raised when an HTTP or network operation against Memos fails."""


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
        raise MemosRequestError(f"Memos HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MemosRequestError(f"Memos request failed: {exc}") from exc


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


STOP_WORDS = [
    "帮我查一下", "帮我查查", "帮我查找", "帮我查询", "帮我查", "帮我找找", "帮我找",
    "帮我看看", "帮我看一下", "查一下", "查查", "查询", "查找", "看看", "看一下",
    "关于", "有关", "涉及", "有没有", "最新的", "最近的", "最新", "最近",
    "几篇", "几条", "几个", "文章", "记录", "备忘", "笔记", "动态", "说说", "内容", "信息",
]


def _clean_query_term(raw_query: str) -> str:
    cleaned = raw_query.strip()
    for sw in sorted(STOP_WORDS, key=len, reverse=True):
        cleaned = cleaned.replace(sw, "")
    cleaned = re.sub(r"[，。！？,.!? \t\n\r\"']", "", cleaned)
    cleaned = cleaned.strip("的").strip()
    return cleaned


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
        try:
            res = _request("/api/v1/memos", payload, method="POST")
        except MemosRequestError as exc:
            raise SystemExit(str(exc)) from exc
        memo_id = res.get("name", "") if isinstance(res, dict) else ""
        result = {
            "reply": "已记录到 Memos。",
            "results": [{"id": memo_id, "memory": text}],
        }
    elif args.command == "search":
        query_text = args.query.strip()
        matched: list[dict[str, Any]] = []
        clean_term = _clean_query_term(query_text)

        try:
            # If query has a clean specific term and no special syntax chars, try CEL filter
            if clean_term and not any(c in clean_term for c in ("'", '"', "\\")):
                try:
                    cel = f"content.contains('{clean_term}')"
                    res = _request("/api/v1/memos", method="GET", query={"filter": cel, "pageSize": 50})
                    if isinstance(res, dict):
                        matched = res.get("memos", [])
                except Exception:
                    pass

            # Fallback or pure recency query
            if not matched:
                res = _request("/api/v1/memos", method="GET", query={"pageSize": 100})
                memos = res.get("memos", []) if isinstance(res, dict) else []
                if not clean_term:
                    # Pure recency query: return the newest memos directly
                    matched = memos
                else:
                    scored: list[tuple[int, dict[str, Any]]] = []
                    term_lower = clean_term.lower()
                    h1 = term_lower[: len(term_lower) // 2] if len(term_lower) >= 4 else ""
                    h2 = term_lower[len(term_lower) // 2 :] if len(term_lower) >= 4 else ""
                    for memo in memos:
                        content = str(memo.get("content", "")).lower()
                        score = 0
                        if term_lower in content:
                            score += 10
                        elif h1 and (h1 in content):
                            score += 3
                        elif h2 and (h2 in content):
                            score += 3
                        if score > 0:
                            scored.append((score, memo))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    matched = [m for _, m in scored]
        except Exception as exc:
            # Gracefully degrade on timeout/error so the agent does not retry
            result = {
                "results": [],
                "count": 0,
                "warning": f"Memos service temporarily unavailable: {exc}",
            }
            json.dump(result, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return

        items = [_format_memo_item(m) for m in matched[: args.limit]]
        result = {
            "results": items,
            "count": len(items),
        }
    elif args.command == "list":
        try:
            res = _request(
                "/api/v1/memos",
                method="GET",
                query={"pageSize": max(1, min(args.page_size, 100))},
            )
            memos = res.get("memos", []) if isinstance(res, dict) else []
        except Exception as exc:
            result = {
                "results": [],
                "count": 0,
                "warning": f"Memos list temporarily unavailable: {exc}",
            }
            json.dump(result, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return
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
        try:
            _request(f"/api/v1/{path_name}", method="DELETE")
        except MemosRequestError as exc:
            raise SystemExit(str(exc)) from exc
        result = {
            "reply": "已删除。",
            "deleted": memory_id,
            "message": "Memory deleted successfully",
        }

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    _main()
