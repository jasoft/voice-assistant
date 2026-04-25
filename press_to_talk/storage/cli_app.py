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
    prog_name = "ptt-storage"

    # Main parser
    parser = AgentFriendlyArgumentParser(
        prog=prog_name,
        description=(
            "Standalone Storage CLI for session history and long-term memory.\n"
            "Designed for agents: successful commands emit machine-readable JSON to stdout."
        ),
        epilog=(
            "Examples:\n"
            f"  {prog_name} --user-id default history list --limit 5\n"
            f"  {prog_name} --user-id default memory search --query \"passport\"\n"
        ),
        formatter_class=AgentHelpFormatter,
    )
    
    # Global arguments (defined only once at top level for clarity)
    parser.add_argument(
        "--user-id",
        help="Target user ID for the operation. (Required)",
    )
    parser.add_argument(
        "-v", "--debug",
        action="store_true",
        help="Enable debug logging.",
    )

    subparsers = parser.add_subparsers(dest="category", required=True)

    # Doctor command
    subparsers.add_parser(
        "doctor",
        help="Check configuration and connectivity of storage backends",
        formatter_class=AgentHelpFormatter,
    )

    # History commands
    history_parser = subparsers.add_parser(
        "history",
        help="Read/write session history records",
        formatter_class=AgentHelpFormatter,
    )
    history_sub = history_parser.add_subparsers(dest="command", required=True)

    h_list = history_sub.add_parser("list", help="List history", formatter_class=AgentHelpFormatter)
    h_list.add_argument("--limit", type=int, default=10)
    h_list.add_argument("--query", default="")

    h_add = history_sub.add_parser("add", help="Add history", formatter_class=AgentHelpFormatter)
    h_add.add_argument("--json", required=True)

    h_del = history_sub.add_parser("delete", help="Delete history", formatter_class=AgentHelpFormatter)
    h_del.add_argument("--session-id", required=True)

    # Memory commands
    memory_parser = subparsers.add_parser(
        "memory",
        help="Read/write long-term memory entries",
        formatter_class=AgentHelpFormatter,
    )
    memory_sub = memory_parser.add_subparsers(dest="command", required=True)

    m_add = memory_sub.add_parser("add", help="Add memory", formatter_class=AgentHelpFormatter)
    m_add.add_argument("--memory", required=True)
    m_add.add_argument("--original-text", default="")

    m_search = memory_sub.add_parser("search", help="Search memory", formatter_class=AgentHelpFormatter)
    m_search.add_argument("--query", required=True)
    m_search.add_argument("--min-score", type=float, default=0.0)
    m_search.add_argument("--start-date")
    m_search.add_argument("--end-date")

    m_del = memory_sub.add_parser("delete", help="Delete memory", formatter_class=AgentHelpFormatter)
    m_del.add_argument("--id", required=True)

    m_update = memory_sub.add_parser("update", help="Update memory", formatter_class=AgentHelpFormatter)
    m_update.add_argument("--id", required=True)
    m_update.add_argument("--memory", required=True)
    m_update.add_argument("--original-text", default="")

    m_list = memory_sub.add_parser("list", help="List memories", formatter_class=AgentHelpFormatter)
    m_list.add_argument("--limit", type=int, default=100)
    m_list.add_argument("--offset", type=int, default=0)

    m_export = memory_sub.add_parser("export", help="Export memories", formatter_class=AgentHelpFormatter)
    m_export.add_argument("--to-provider", required=True, choices=["mem0", "sqlite_fts5"])

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

    # Get input args
    input_args = list(sys.argv[1:] if argv is None else argv)
    
    # Pre-interception for 'doctor' (legacy behavior)
    if input_args and input_args[0] == "doctor":
        return _run_storage_doctor()

    parser = build_parser()
    
    # Pre-parse just to see if we have help request or no subcommand
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument("--user-id")
    global_parser.add_argument("-v", "--debug", action="store_true")
    global_args, remaining_argv = global_parser.parse_known_args(input_args)

    # 2. 帮助信息处理
    if not input_args or "-h" in input_args or "--help" in input_args:
        parser.print_help()
        return 0

    # If only global args are present but no action, show help and return 0
    if not remaining_argv:
        parser.print_help()
        return 0
    
    # 3. 解析正式参数
    try:
        args = parser.parse_args(input_args)
    except SystemExit as e:
        raise # Re-raise for error reporting and tests

    # 4. 强制要求 user_id
    effective_user_id = args.user_id or global_args.user_id
    if not effective_user_id:
        parser.error("argument --user-id is required")

    from press_to_talk.utils.logging import set_global_log_level
    from press_to_talk.storage.memory_backends import export_memories_to_provider

    if args.debug or global_args.debug:
        set_global_log_level("DEBUG")
    else:
        set_global_log_level("INFO")

    stderr_buffer = io.StringIO()
    try:
        exit_code = 0
        with contextlib.redirect_stderr(stderr_buffer):
            config = load_storage_config(user_id_override=effective_user_id)
            
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
