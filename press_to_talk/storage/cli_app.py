from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import SessionHistoryRecord, StorageConfig, RememberItemRecord
from .service import StorageService, load_storage_config, APP_ROOT


class AgentHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Custom help formatter that provides a consistent look for agents."""

    def add_usage(self, usage, actions, groups, prefix=None):
        if prefix is None:
            prefix = "Usage: "
        return super().add_usage(usage, actions, groups, prefix)


class AgentFriendlyArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser with better error reporting for agents."""

    def error(self, message: str) -> None:
        full_message = f"{message}\n\n"
        
        # Add spelling suggestions for invalid choices
        invalid_choice_match = re.search(r"invalid choice: '([^']+)' \(choose from ([^)]+)\)", message)
        if invalid_choice_match:
            offered = invalid_choice_match.group(1)
            choices = [c.strip() for c in invalid_choice_match.group(2).split(",")]
            matches = difflib.get_close_matches(offered, choices, n=1, cutoff=0.6)
            if matches:
                full_message = f"invalid choice: '{offered}'. Did you mean '{matches[0]}'?\n\n"

        full_message += self.format_help()
        self.exit(2, f"{self.prog}: error: {full_message}\n")


def build_parser() -> argparse.ArgumentParser:
    prog_name = Path(sys.argv[0]).name
    if prog_name == "cli_app.py" or prog_name == "__main__.py":
        prog_name = "python3 -m press_to_talk.storage.cli_app"

    # Define base parser with shared arguments to be inherited by action-performing subparsers
    base_parser = argparse.ArgumentParser(add_help=False)
    base_parser.add_argument(
        "--user-id",
        default=None,
        help="Target user ID for the operation. If not provided, uses defaults from config/env.",
    )
    base_parser.add_argument(
        "-v", "--debug",
        action="store_true",
        help="Enable debug logging for storage operations.",
    )

    parser = AgentFriendlyArgumentParser(
        prog=prog_name,
        description=(
            "Standalone Storage CLI for session history and long-term memory.\n"
            "Designed for agents: successful commands emit machine-readable JSON to stdout."
        ),
        epilog=(
            "Examples:\n"
            f"  {prog_name} history list --limit 5\n"
            f"  {prog_name} history list --query \"passport\"\n"
            f"  {prog_name} history add --json '{{\"session_id\":\"abc\",...}}'\n"
            f"  {prog_name} memory search --query 'usb'\n"
            f"  {prog_name} memory search --query '\"usb\" OR \"测试版\"' | jq '.results[] | .memory'\n"
            f"  {prog_name} memory add --memory \"The passport is in the top drawer\" --original-text \"My passport is in the top drawer\"\n"
            f"  {prog_name} memory delete --id <uuid>\n"
        ),
        formatter_class=AgentHelpFormatter,
    )
    
    # Add top-level global arguments (for leading usage like: ptt-storage -v history list)
    parser.add_argument(
        "--user-id",
        default=None,
        help="Target user ID for the operation. (Top-level)",
    )
    parser.add_argument(
        "-v", "--debug",
        action="store_true",
        help="Enable debug logging. (Top-level)",
    )

    subparsers = parser.add_subparsers(dest="category", required=True)

    # Doctor command
    doctor_parser = subparsers.add_parser(
        "doctor",
        parents=[base_parser],
        help="Check configuration and connectivity of storage backends",
        description=f"Verify environment variables and database accessibility. Run as `{prog_name} doctor`.",
        formatter_class=AgentHelpFormatter,
    )

    # History commands
    history_parser = subparsers.add_parser(
        "history",
        help="Read/write session history records",
        description=(
            "Session history CRUD.\n"
            "Use this to list, insert, or delete past conversation records."
        ),
        formatter_class=AgentHelpFormatter,
    )
    history_sub = history_parser.add_subparsers(dest="command", required=True)

    h_list = history_sub.add_parser(
        "list",
        parents=[base_parser],
        help="List history records as JSON",
        description="Return recent session history records as a JSON array.",
        formatter_class=AgentHelpFormatter,
    )
    h_list.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of records to return, ordered by newest first.",
    )
    h_list.add_argument(
        "--query",
        default="",
        help="Optional substring filter applied to transcript and reply fields.",
    )

    h_add = history_sub.add_parser(
        "add",
        parents=[base_parser],
        help="Insert or update one history record",
        description="Persist one complete session history record from a JSON object.",
        formatter_class=AgentHelpFormatter,
    )
    h_add.add_argument(
        "--json",
        required=True,
        help="Full SessionHistoryRecord JSON object with session_id, timestamps, transcript, reply, levels, and mode.",
    )

    h_del = history_sub.add_parser(
        "delete",
        parents=[base_parser],
        help="Delete one history record by session_id",
        description="Delete exactly one history record using its session_id.",
        formatter_class=AgentHelpFormatter,
    )
    h_del.add_argument(
        "--session-id",
        required=True,
        help="Unique session_id of the history record to delete.",
    )

    # Memory commands
    memory_parser = subparsers.add_parser(
        "memory",
        help="Read/write long-term memory entries",
        description=(
            "Long-term memory CRUD.\n"
            "Search returns JSON shaped as {\"results\": [...]} so it can be piped to jq."
        ),
        formatter_class=AgentHelpFormatter,
    )
    memory_sub = memory_parser.add_subparsers(dest="command", required=True)

    m_add = memory_sub.add_parser(
        "add",
        parents=[base_parser],
        help="Insert one memory entry",
        description="Persist one long-term memory entry and its original source text.",
        formatter_class=AgentHelpFormatter,
    )
    m_add.add_argument("--memory", required=True, help="Cleaned memory text to store.")
    m_add.add_argument(
        "--original-text",
        default="",
        help="Optional raw source text or transcript associated with the memory.",
    )

    m_search = memory_sub.add_parser(
        "search",
        parents=[base_parser],
        help="Search memory entries and return JSON results",
        description=(
            "Search long-term memory entries.\n"
            "For sqlite simple_query, pass keywords and let the storage layer split them."
        ),
        formatter_class=AgentHelpFormatter,
    )
    m_search.add_argument("--query", required=True, help="Search keywords or sentence.")
    m_search.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum similarity score threshold (0.0 to 1.0).",
    )
    m_search.add_argument(
        "--start-date",
        help="Start date for range query (YYYY-MM-DD).",
    )
    m_search.add_argument(
        "--end-date",
        help="End date for range query (YYYY-MM-DD).",
    )

    m_del = memory_sub.add_parser(
        "delete",
        parents=[base_parser],
        help="Delete one memory entry by id",
        description="Delete exactly one memory entry using its unique id.",
        formatter_class=AgentHelpFormatter,
    )
    m_del.add_argument("--id", required=True, help="Unique memory id to delete.")

    m_update = memory_sub.add_parser(
        "update",
        parents=[base_parser],
        help="Update one memory entry by id",
        description="Update exactly one memory entry using its unique id.",
        formatter_class=AgentHelpFormatter,
    )
    m_update.add_argument("--id", required=True, help="Unique memory id to delete.")
    m_update.add_argument("--memory", required=True, help="New memory text to store.")
    m_update.add_argument(
        "--original-text",
        default="",
        help="Optional updated raw source text associated with the memory.",
    )

    m_list = memory_sub.add_parser(
        "list",
        parents=[base_parser],
        help="List memory entries as JSON",
        description="Return stored memory entries as a JSON array.",
        formatter_class=AgentHelpFormatter,
    )
    m_list.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of entries to return.",
    )
    m_list.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of memory entries to skip for pagination.",
    )

    m_export = memory_sub.add_parser(
        "export",
        parents=[base_parser],
        help="Export local memories to another provider (e.g., mem0)",
        description="Migrate all local SQLite memories to a cloud provider like mem0.",
        formatter_class=AgentHelpFormatter,
    )
    m_export.add_argument(
        "--to-provider",
        required=True,
        choices=["mem0", "sqlite_fts5"],
        help="Target provider to export to.",
    )

    return parser


def _build_local_service() -> StorageService:
    config = load_storage_config()
    return StorageService(config, use_cli=False)


def _run_storage_doctor() -> int:
    try:
        service = _build_local_service()
        report = service.diagnose()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("status") == "ok" else 1
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1


def main(argv: list[str] | None = None) -> int:
    from ..utils.env import load_env_files
    load_env_files()

    parsed_argv = list(sys.argv[1:] if argv is None else argv)
    
    # Pre-interception for 'doctor'
    if parsed_argv and parsed_argv[0] == "doctor":
        return _run_storage_doctor()

    parser = build_parser()
    if not parsed_argv:
        parser.print_help()
        return 0
    
    args = parser.parse_args(parsed_argv)

    from press_to_talk.utils.logging import set_global_log_level
    from press_to_talk.storage.memory_backends import export_memories_to_provider

    # Merge top-level and sub-command arguments
    # (In nested argparse, the last seen argument wins, but parents might override)
    # We prioritize sub-command arguments if they were provided (not None)
    # But argparse handles this automatically if same dest.
    # The real issue is debug vs -v and inheritance.

    if args.debug:
        set_global_log_level("DEBUG")
    else:
        set_global_log_level("INFO")

    stderr_buffer = io.StringIO()
    try:
        exit_code = 0
        with contextlib.redirect_stderr(stderr_buffer):
            config = load_storage_config(user_id_override=args.user_id)
            if args.category == "doctor":
                return _run_storage_doctor()

            service = StorageService(config, use_cli=False)
            if args.category == "memory" and args.command == "search":
                print(service.remember_store().find(
                    query=args.query,
                    min_score=args.min_score,
                    start_date=args.start_date,
                    end_date=args.end_date
                ))
            elif args.category == "memory" and args.command == "add":
                print(
                    json.dumps(
                        {"result": service.remember_store().add(memory=args.memory, original_text=args.original_text)},
                        ensure_ascii=False
                    )
                )
            elif args.category == "memory" and args.command == "delete":
                service.remember_store().delete(memory_id=args.id)
                print(json.dumps({"deleted": args.id}, ensure_ascii=False))
            elif args.category == "memory" and args.command == "update":
                record = service.remember_store().update(
                    memory_id=args.id,
                    memory=args.memory,
                    original_text=args.original_text,
                )
                d = asdict(record)
                d.pop("source_memory_id", None)
                print(json.dumps({"updated": d}, ensure_ascii=False))
            elif args.category == "memory" and args.command == "list":
                records = service.remember_store().list_all(limit=args.limit, offset=args.offset)
                output_list = []
                for r in records:
                    d = asdict(r)
                    d.pop("source_memory_id", None)
                    output_list.append(d)
                print(json.dumps(output_list, ensure_ascii=False))
            elif args.category == "memory" and args.command == "export":
                from .memory_backends import get_remember_provider_class
                target_cls = get_remember_provider_class(args.to_provider)
                target_store = target_cls.from_config(config)
                count = export_memories_to_provider(
                    source_store=service.remember_store(),
                    target_store=target_store
                )
                print(json.dumps({"status": "ok", "exported_count": count}, ensure_ascii=False))
            elif args.category == "history" and args.command == "list":
                records = service.history_store().list_recent(limit=args.limit, query=args.query)
                print(json.dumps([asdict(r) for r in records], ensure_ascii=False))
            elif args.category == "history" and args.command == "add":
                data = json.loads(args.json)
                service.history_store().persist(SessionHistoryRecord(**data))
                print(json.dumps({"status": "ok"}, ensure_ascii=False))
            elif args.category == "history" and args.command == "delete":
                service.history_store().delete(session_id=args.session_id)
                print(json.dumps({"deleted": args.session_id}, ensure_ascii=False))

        buffered = stderr_buffer.getvalue()
        if buffered:
            sys.stderr.write(buffered)
            sys.stderr.flush()
        return exit_code
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
