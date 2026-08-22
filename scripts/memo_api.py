#!/usr/bin/env python3
"""Minimal command-line access to the Mem0 scope used by the memo agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request


BASE_URL = "https://api.mem0.ai"
USER_ID = "soj"


def _token() -> str:
    token = os.environ.get("MEM0_API_KEY") or os.environ.get("MEM0_MCP_TOKEN") or ""
    if not token:
        raise SystemExit("MEM0_API_KEY or MEM0_MCP_TOKEN is required")
    return token


def _request(path: str, payload: dict[str, object], *, query: dict[str, int] | None = None) -> object:
    token = _token()
    url = BASE_URL + path
    if query:
        from urllib.parse import urlencode

        url += "?" + urlencode(query)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
            # Mem0's client derives this value from the token; it scopes API access.
            "Mem0-User-ID": hashlib.md5(token.encode()).hexdigest(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Mem0 HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(f"Mem0 request failed: {exc}") from exc


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

    args = parser.parse_args(argv)
    if args.command == "add":
        payload = {
            "messages": [{"role": "user", "content": args.text}],
            "user_id": USER_ID,
            "infer": False,
            "async_mode": False,
            "output_format": "v1.1",
        }
        result = _request("/v1/memories/", payload)
    elif args.command == "search":
        result = _request(
            "/v2/memories/search/",
            {
                "query": args.query,
                "filters": {"AND": [{"user_id": USER_ID}]},
                "limit": max(1, min(args.limit, 100)),
                "rerank": True,
            },
        )
    else:
        result = _request(
            "/v2/memories/",
            {"filters": {"AND": [{"user_id": USER_ID}]}},
            query={"page": max(1, args.page), "page_size": max(1, min(args.page_size, 100))},
        )

    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    _main()
