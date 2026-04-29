from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import SessionHistoryRecord, StorageConfig, RememberItemRecord
from .service import (
    StorageService,
    load_storage_config,
    APP_ROOT,
    resolve_user_id_from_api_key,
)


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
            f"  {prog_name} --api-key <your-token> history list --limit 5\n"
            f"  {prog_name} --api-key <your-token> memory search --query \"passport\"\n"
        ),
        formatter_class=AgentHelpFormatter,
    )

    # Global arguments (pre-processed in main)
    parser.add_argument(
        "--api-key",
        "--token",
        dest="api_key",
        help="API Token to identify the user.",
    )
    parser.add_argument(
        "--user-id",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-v", "--debug",
        action="store_true",
        help="Enable debug logging.",
    )

    subparsers = parser.add_subparsers(dest="category", required=True)

    # Subparsers for commands (inherited base parser logic is now handled manually in main)
    subparsers.add_parser("doctor", help="Check status", formatter_class=AgentHelpFormatter)

    # History
    history_parser = subparsers.add_parser("history", help="History CRUD", formatter_class=AgentHelpFormatter)
    history_sub = history_parser.add_subparsers(dest="command", required=True)

    h_list = history_sub.add_parser("list", help="List history", formatter_class=AgentHelpFormatter)
    h_list.add_argument("--limit", type=int, default=10)
    h_list.add_argument("--query", default="")

    h_add = history_sub.add_parser("add", help="Add history", formatter_class=AgentHelpFormatter)
    h_add.add_argument("--json", required=True)

    h_del = history_sub.add_parser("delete", help="Delete history", formatter_class=AgentHelpFormatter)
    h_del.add_argument("--session-id", required=True)

    # Memory
    memory_parser = subparsers.add_parser("memory", help="Memory CRUD", formatter_class=AgentHelpFormatter)
    memory_sub = memory_parser.add_subparsers(dest="command", required=True)

    m_add = memory_sub.add_parser("add", help="Add memory", formatter_class=AgentHelpFormatter)
    m_add.add_argument("--memory", required=True)
    m_add.add_argument("--original-text", default="")
    m_add.add_argument("--photo-path", help="Relative path to the photo file.")

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
    m_update.add_argument("--photo-path", help="Relative path to the photo file.")

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

    input_args = list(sys.argv[1:] if argv is None else argv)

    if input_args and input_args[0] == "doctor":
        return _run_storage_doctor()

    parser = build_parser()
    if not input_args:
        parser.print_help()
        return 0

    # 1. 预处理全局参数 (不带 help)
    # 这样可以处理像 ptt-storage --api-key xxx memory search -h 这样的命令
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument("--api-key", "--token", dest="api_key")
    global_parser.add_argument("--user-id")
    global_parser.add_argument("-v", "--debug", action="store_true")
    global_args, remaining_argv = global_parser.parse_known_args(input_args)

    # 如果 remaining_argv 为空且不是请求帮助，打印主帮助
    if not remaining_argv and "-h" not in input_args and "--help" not in input_args:
        parser.print_help()
        return 0

    # 2. 解析剩余参数 (包括子命令和子命令的 -h)
    args = parser.parse_args(remaining_argv)

    # 2. 身份识别逻辑 (Token优先)
    effective_user_id = None
    cli_api_key = (global_args.api_key or "").strip()
    env_api_key = (
        os.environ.get("PTT_API_KEY")
        or os.environ.get("PTT_USER_API_KEY")
        or ""
    ).strip()
    explicit_user_id = global_args.user_id
    api_key = cli_api_key or ("" if explicit_user_id else env_api_key)
    if api_key:
        effective_user_id = resolve_user_id_from_api_key(api_key)
        if not effective_user_id:
            parser.error(f"invalid --api-key: {api_key}")

    # 3. 兜底使用 user_id (Admin 模式)
    if not effective_user_id:
        effective_user_id = explicit_user_id

    # 4. 强制校验
    if not effective_user_id:
        parser.error("the following arguments are required: --api-key")

    from press_to_talk.utils.logging import set_global_log_level
    from press_to_talk.storage.memory_backends import export_memories_to_provider

    if global_args.debug:
        set_global_log_level("DEBUG")
    else:
        set_global_log_level("INFO")

    stderr_buffer = io.StringIO()
    try:
        exit_code = 0
        with contextlib.redirect_stderr(stderr_buffer):
            config = load_storage_config(
                user_id_override=effective_user_id,
                api_key_override=api_key or None,
            )

            if args.category == "doctor":
                return _run_storage_doctor()

            service = StorageService(config, use_cli=False)

            def _archive_photo(input_path: str | None) -> str | None:
                if not input_path:
                    return None
                
                import shutil
                import uuid
                from datetime import datetime
                
                src = Path(input_path).expanduser().resolve()
                if not src.exists():
                    return input_path # 保持原样，让底层报错或处理
                
                # 目标目录
                photos_dir = APP_ROOT / "data" / "photos"
                photos_dir.mkdir(parents=True, exist_ok=True)
                
                # 如果已经在 photos 目录里了，就只返回相对路径
                try:
                    if src.is_relative_to(photos_dir):
                        return str(src.relative_to(APP_ROOT / "data"))
                except ValueError:
                    pass
                
                # 否则，复制进去
                ext = src.suffix or ".jpg"
                new_filename = f"photo_cli_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
                dest = photos_dir / new_filename
                shutil.copy2(src, dest)
                return f"photos/{new_filename}"

            if args.category == "memory" and args.command == "search":
                print(service.remember_store().find(
                    query=args.query,
                    min_score=args.min_score,
                    start_date=args.start_date,
                    end_date=args.end_date
                ))
            elif args.category == "memory" and args.command == "add":
                final_photo_path = _archive_photo(args.photo_path)
                print(json.dumps({"result": service.remember_store().add(
                    memory=args.memory, 
                    original_text=args.original_text,
                    photo_path=final_photo_path
                )}, ensure_ascii=False))
            elif args.category == "memory" and args.command == "delete":
                service.remember_store().delete(memory_id=args.id)
                print(json.dumps({"deleted": args.id}, ensure_ascii=False))
            elif args.category == "memory" and args.command == "update":
                final_photo_path = _archive_photo(args.photo_path)
                record = service.remember_store().update(
                    memory_id=args.id, 
                    memory=args.memory, 
                    original_text=args.original_text,
                    photo_path=final_photo_path
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
                count = export_memories_to_provider(source_store=service.remember_store(), target_store=target_store)
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


def run_as_console_script() -> int:
    return main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
