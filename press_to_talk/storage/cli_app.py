from __future__ import annotations

import argparse
import contextlib
import io
import json
from dataclasses import asdict

from .models import SessionHistoryRecord
from .service import StorageService, load_storage_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="press-to-talk-storage",
        description="Standalone Storage CLI for session history and long-term memory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="category", required=True)

    history_parser = subparsers.add_parser("history", help="Manage session history records")
    history_sub = history_parser.add_subparsers(dest="command", required=True)
    h_list = history_sub.add_parser("list", help="List recent history records as JSON")
    h_list.add_argument("--limit", type=int, default=10)
    h_list.add_argument("--query", default="")
    h_add = history_sub.add_parser("add", help="Save a new history record")
    h_add.add_argument("--json", required=True)
    h_del = history_sub.add_parser("delete", help="Delete a history record by session ID")
    h_del.add_argument("--session-id", required=True)

    memory_parser = subparsers.add_parser("memory", help="Manage long-term memory entries")
    memory_sub = memory_parser.add_subparsers(dest="command", required=True)
    m_add = memory_sub.add_parser("add", help="Add a new long-term memory")
    m_add.add_argument("--memory", required=True)
    m_add.add_argument("--original-text", default="")
    m_search = memory_sub.add_parser("search", help="Search memories using FTS5")
    m_search.add_argument("--query", required=True)
    m_del = memory_sub.add_parser("delete", help="Delete a memory entry by its ID")
    m_del.add_argument("--id", required=True)
    m_list = memory_sub.add_parser("list", help="List all stored memories")
    m_list.add_argument("--limit", type=int, default=100)
    return parser


def _build_local_service() -> StorageService:
    config = load_storage_config()
    config.query_rewrite_enabled = False
    return StorageService(config, use_cli=False)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from press_to_talk.core import load_env_files

    load_env_files()

    try:
        with contextlib.redirect_stderr(io.StringIO()):
            service = _build_local_service()
            if args.category == "memory" and args.command == "search":
                print(service.remember_store().find(query=args.query))
                return 0

            if args.category == "history":
                store = service.history_store()
                if args.command == "list":
                    records = store.list_recent(limit=args.limit, query=args.query)
                    print(json.dumps([asdict(record) for record in records], ensure_ascii=False))
                elif args.command == "add":
                    data = json.loads(args.json)
                    store.persist(SessionHistoryRecord(**data))
                    print(json.dumps({"status": "ok"}))
                elif args.command == "delete":
                    store.delete(session_id=args.session_id)
                    print(json.dumps({"deleted": args.session_id}))
            elif args.category == "memory":
                store = service.remember_store()
                if args.command == "add":
                    print(
                        json.dumps(
                            {"result": store.add(memory=args.memory, original_text=args.original_text)}
                        )
                    )
                elif args.command == "delete":
                    store.delete(memory_id=args.id)
                    print(json.dumps({"deleted": args.id}))
                elif args.command == "list":
                    records = store.list_all(limit=args.limit)
                    print(json.dumps([asdict(record) for record in records], ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
