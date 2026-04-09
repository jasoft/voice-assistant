from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .storage import StorageService


def main() -> int:
    parser = argparse.ArgumentParser(prog="press-to-talk-storage")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_history = subparsers.add_parser("list-history", help="List recent history records as JSON")
    parser_history.add_argument("--limit", type=int, default=10)
    parser_history.add_argument("--query", default="", help="Search history records by transcript or reply")

    subparsers.add_parser("sync-nocodb-to-sqlite", help="Copy NocoDB remember/history data into local sqlite")

    args = parser.parse_args()
    from .core import load_env_files

    load_env_files()

    try:
        service = StorageService.from_env()

        if args.command == "list-history":
            records = service.history_store().list_recent(
                limit=max(1, args.limit),
                query=str(args.query or "").strip(),
            )
            print(json.dumps([asdict(item) for item in records], ensure_ascii=False))
            return 0

        if args.command == "sync-nocodb-to-sqlite":
            summary = service.sync_nocodb_to_sqlite()
            print(json.dumps(summary, ensure_ascii=False))
            return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
