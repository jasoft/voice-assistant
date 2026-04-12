from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any
from urllib import error, request


MEM0_API_BASE = "https://api.mem0.ai"


def api_request(
    *,
    api_key: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    body = None
    headers = {"Authorization": f"Token {api_key}"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(f"{MEM0_API_BASE}{path}", data=body, headers=headers, method=method)
    try:
        with request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc


def fetch_all_memories(*, api_key: str, user_id: str, page_size: int) -> list[dict[str, Any]]:
    page = 1
    results: list[dict[str, Any]] = []
    while True:
        payload = {
            "filters": {"AND": [{"user_id": user_id}]},
            "page_size": page_size,
            "page": page,
        }
        response = api_request(api_key=api_key, method="POST", path="/v2/memories/", payload=payload)
        if isinstance(response, list):
            batch = response
        elif isinstance(response, dict):
            batch = response.get("results", [])
        else:
            batch = []
        if not isinstance(batch, list) or not batch:
            break
        results.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < page_size:
            break
        page += 1
    return results


def clone_memory_with_app_id(
    *,
    api_key: str,
    user_id: str,
    app_id: str,
    memory: dict[str, Any],
) -> str:
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": str(memory.get("memory", ""))}],
        "user_id": user_id,
        "app_id": app_id,
        "async_mode": False,
        "infer": False,
    }
    metadata = memory.get("metadata")
    if metadata is not None:
        payload["metadata"] = metadata
    created_at = memory.get("created_at")
    if created_at:
        normalized_created_at = str(created_at).replace("Z", "+00:00")
        payload["timestamp"] = int(datetime.fromisoformat(normalized_created_at).timestamp())
    response = api_request(api_key=api_key, method="POST", path="/v1/memories/", payload=payload)
    items = response.get("results", response if isinstance(response, list) else [])
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"unexpected add response for memory {memory.get('id')}: {response}")
    first = items[0]
    if not isinstance(first, dict) or not first.get("id"):
        raise RuntimeError(f"missing cloned memory id for {memory.get('id')}: {response}")
    return str(first["id"])


def delete_memory(*, api_key: str, memory_id: str) -> None:
    api_request(api_key=api_key, method="DELETE", path=f"/v1/memories/{memory_id}/")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill mem0 app_id by cloning old memories and deleting originals.")
    parser.add_argument("--user-id", default=os.environ.get("MEM0_USER_ID", "soj"))
    parser.add_argument("--app-id", default="voice-assistant")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--apply", action="store_true", help="Actually clone and delete. Default is dry-run.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("MEM0_API_KEY", "").strip()
    if not api_key:
        print("MEM0_API_KEY is required", file=sys.stderr)
        return 1

    memories = fetch_all_memories(api_key=api_key, user_id=args.user_id, page_size=args.page_size)
    pending = [item for item in memories if item.get("app_id") != args.app_id]
    print(
        json.dumps(
            {
                "user_id": args.user_id,
                "target_app_id": args.app_id,
                "total_memories": len(memories),
                "pending_migration": len(pending),
                "dry_run": not args.apply,
            },
            ensure_ascii=False,
        )
    )
    if not args.apply:
        return 0

    migrated: list[dict[str, str]] = []
    for item in pending:
        old_id = str(item.get("id", "")).strip()
        if not old_id:
            continue
        new_id = clone_memory_with_app_id(
            api_key=api_key,
            user_id=args.user_id,
            app_id=args.app_id,
            memory=item,
        )
        delete_memory(api_key=api_key, memory_id=old_id)
        migrated.append({"old_id": old_id, "new_id": new_id})
        print(json.dumps(migrated[-1], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
