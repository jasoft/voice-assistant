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

from .models import SessionHistoryRecord
from .service import StorageService, load_storage_config


class AgentHelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
):
    pass


class AgentFriendlyArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        suggestion = self._suggestion_from_error(message)
        full_message = message if not suggestion else f"{message}\n\nDid you mean '{suggestion}'?"
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {full_message}\n")

    def _suggestion_from_error(self, message: str) -> str:
        matched = re.search(r"invalid choice: '([^']+)'.*choose from ([^)]+)", message)
        if not matched:
            return ""
        invalid = matched.group(1)
        choices = [item.strip().strip("'") for item in matched.group(2).split(",")]
        suggestions = difflib.get_close_matches(invalid, choices, n=1, cutoff=0.5)
        return suggestions[0] if suggestions else ""

def build_parser() -> argparse.ArgumentParser:
    prog_name = Path(sys.argv[0]).name
    if prog_name == "cli_app.py" or prog_name == "__main__.py":
        prog_name = "python3 -m press_to_talk.storage.cli_app"

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
    subparsers = parser.add_subparsers(dest="category", required=True)

    # Doctor command
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check configuration and connectivity of storage backends",
        description=f"Verify environment variables and database accessibility. Run as `{prog_name} doctor`.",
        formatter_class=AgentHelpFormatter,
    )

    history_parser = subparsers.add_parser(
        "history",
        help="Read/write session history records",
        description=(
            "Session history CRUD.\n"
            "Use this to list, insert, or delete past conversation records."
        ),
        formatter_class=AgentHelpFormatter,
        epilog=(
            "Examples:\n"
            f"  {prog_name} history list --limit 3\n"
            f"  {prog_name} history list --query \"壮壮\"\n"
            f"  {prog_name} history delete --session-id 4315f703e74248839604be6ad6349f8a\n"
        ),
    )
    history_sub = history_parser.add_subparsers(dest="command", required=True)
    h_list = history_sub.add_parser(
        "list",
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
        help="Delete one history record by session_id",
        description="Delete exactly one history record using its session_id.",
        formatter_class=AgentHelpFormatter,
    )
    h_del.add_argument(
        "--session-id",
        required=True,
        help="Unique session_id of the history record to delete.",
    )

    memory_parser = subparsers.add_parser(
        "memory",
        help="Read/write long-term memory entries",
        description=(
            "Long-term memory CRUD.\n"
            "Search returns JSON shaped as {\"results\": [...]} so it can be piped to jq."
        ),
        formatter_class=AgentHelpFormatter,
        epilog=(
            "Examples:\n"
            f"  {prog_name} memory search --query 'usb'\n"
            f"  {prog_name} memory search --query '\"usb\" OR \"测试版\"' | jq -r '.results[] | \"\\(.memory)\\t\\(.created_at)\"'\n"
            f"  {prog_name} memory list --limit 20\n"
            f"  {prog_name} memory update --id 8eb8825167ad406ba2501e83c2eb24c8 --memory \"护照在卧室第二层抽屉\" --original-text \"帮我改成护照在卧室第二层抽屉\"\n"
            f"  {prog_name} memory delete --id 8eb8825167ad406ba2501e83c2eb24c8\n"
        ),
    )
    memory_sub = memory_parser.add_subparsers(dest="command", required=True)
    m_add = memory_sub.add_parser(
        "add",
        help="Insert one memory entry",
        description="Persist one long-term memory entry and its original source text.",
        formatter_class=AgentHelpFormatter,
    )
    m_add.add_argument(
        "--memory",
        required=True,
        help="Canonical memory text to store; this is the searchable normalized content.",
    )
    m_add.add_argument(
        "--original-text",
        default="",
        help="Optional raw source text or transcript associated with the memory.",
    )
    m_search = memory_sub.add_parser(
        "search",
        help="Search memory entries and return JSON results",
        description=(
            "Search long-term memory entries.\n"
            "For sqlite simple_query, pass keywords and let the storage layer split them."
        ),
        formatter_class=AgentHelpFormatter,
    )
    m_search.add_argument(
        "--query",
        required=True,
        help="Search query text. Can be raw keywords or a pre-rewritten query; output is JSON to stdout.",
    )
    m_search.add_argument(
        "--min-score",
        type=float,
        default=0.5,
        help="Minimum score threshold for search results (default: 0.5).",
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
        help="Delete one memory entry by id",
        description="Delete exactly one memory entry using its unique id.",
        formatter_class=AgentHelpFormatter,
    )
    m_del.add_argument(
        "--id",
        required=True,
        help="Unique memory id to delete.",
    )
    m_update = memory_sub.add_parser(
        "update",
        help="Update one memory entry by id",
        description="Update exactly one memory entry using its unique id.",
        formatter_class=AgentHelpFormatter,
    )
    m_update.add_argument(
        "--id",
        required=True,
        help="Unique memory id to update.",
    )
    m_update.add_argument(
        "--memory",
        required=True,
        help="Updated canonical memory text to store.",
    )
    m_update.add_argument(
        "--original-text",
        default="",
        help="Optional updated raw source text associated with the memory.",
    )
    m_list = memory_sub.add_parser(
        "list",
        help="List memory entries as JSON",
        description="Return stored memory entries as a JSON array.",
        formatter_class=AgentHelpFormatter,
    )
    m_list.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of memory entries to return, ordered by newest first.",
    )
    m_list.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of memory entries to skip for pagination.",
    )
    m_export = memory_sub.add_parser(
        "export",
        help="Export local memories to another provider (e.g., mem0)",
        description="Migrate all local SQLite memories to a cloud provider like mem0. Uses user_id=soj and no app_id.",
        formatter_class=AgentHelpFormatter,
    )
    m_export.add_argument(
        "--to-provider",
        required=True,
        choices=["mem0", "sqlite_fts5"],
        help="Target provider to export to.",
    )
    parser.add_argument(
        "-v", "--debug",
        action="store_true",
        help="Enable debug logging for storage operations.",
    )
    return parser


def _build_local_service() -> StorageService:
    config = load_storage_config()
    return StorageService(config, use_cli=False)


def _run_storage_doctor() -> int:
    from press_to_talk.core import load_env_files
    load_env_files()
    config = load_storage_config()
    report = {
        "status": "ok",
        "config": {
            "backend": config.backend,
            "history_db": str(config.history_db_path),
            "remember_db": str(config.remember_db_path),
            "llm_model": config.llm_model,
        },
        "connectivity": {},
    }
    # Check SQLite
    try:
        import sqlite3
        for db_key, db_path in [("history", config.history_db_path), ("remember", config.remember_db_path)]:
            path = Path(db_path)
            if not path.parent.exists():
                report["connectivity"][f"{db_key}_db"] = f"error: parent dir {path.parent} missing"
                report["status"] = "error"
                continue
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT 1")
            conn.close()
            report["connectivity"][f"{db_key}_db"] = "connected"
    except Exception as e:
        report["connectivity"]["sqlite"] = f"error: {str(e)}"
        report["status"] = "error"

    # Check LLM
    if config.llm_api_key:
        report["connectivity"]["llm_api"] = "configured (key present)"
    else:
        report["connectivity"]["llm_api"] = "missing (OPENAI_API_KEY)"
        if config.backend == "sqlite_fts5" and config.query_rewrite_enabled:
             report["status"] = "warn"

    # Check Mem0
    if config.backend == "mem0":
        if config.mem0_api_key:
             report["connectivity"]["mem0_api"] = "configured (key present)"
        else:
             report["connectivity"]["mem0_api"] = "missing (MEM0_API_KEY)"
             report["status"] = "error"

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] != "error" else 1


def run_as_console_script() -> int:
    return main()


def main(argv: list[str] | None = None) -> int:
    from ..utils.env import load_env_files
    load_env_files()

    parsed_argv = list(sys.argv[1:] if argv is None else argv)
    
    # Intercept 'doctor' for ptt-storage
    if parsed_argv and parsed_argv[0] == "doctor":
        return _run_storage_doctor()

    parser = build_parser()
    if not parsed_argv:
        parser.print_help()
        return 0
    args = parser.parse_args(parsed_argv)

    from press_to_talk.core import load_env_files
    from press_to_talk.utils.logging import set_global_log_level
    from press_to_talk.storage.memory_backends import export_memories_to_provider

    load_env_files()
    if args.debug:
        set_global_log_level("DEBUG")
    else:
        set_global_log_level("INFO")

    stderr_buffer = io.StringIO()
    try:
        exit_code = 0
        with contextlib.redirect_stderr(stderr_buffer):
            config = load_storage_config()
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
                exit_code = 0
            elif args.category == "memory" and args.command == "export":
                target_store = service.build_export_target_store(args.to_provider)
                source_store = service.remember_store()
                count = export_memories_to_provider(
                    source_store=source_store,
                    target_store=target_store
                )
                print(json.dumps({"status": "ok", "exported_count": count}, ensure_ascii=False))
                exit_code = 0
            elif args.category == "history":
                store = service.history_store()
                if args.command == "list":
                    records = store.list_recent(limit=args.limit, query=args.query)
                    print(json.dumps([asdict(record) for record in records], ensure_ascii=False))
                elif args.command == "add":
                    data = json.loads(args.json)
                    store.persist(SessionHistoryRecord(**data))
                    print(json.dumps({"status": "ok"}, ensure_ascii=False))
                elif args.command == "delete":
                    store.delete(session_id=args.session_id)
                    print(json.dumps({"deleted": args.session_id}, ensure_ascii=False))
            elif args.category == "memory":
                store = service.remember_store()
                if args.command == "add":
                    print(
                        json.dumps(
                            {"result": store.add(memory=args.memory, original_text=args.original_text)},
                            ensure_ascii=False
                        )
                    )
                elif args.command == "delete":
                    store.delete(memory_id=args.id)
                    print(json.dumps({"deleted": args.id}, ensure_ascii=False))
                elif args.command == "update":
                    record = store.update(
                        memory_id=args.id,
                        memory=args.memory,
                        original_text=args.original_text,
                    )
                    print(json.dumps({"updated": asdict(record)}, ensure_ascii=False))
                elif args.command == "list":
                    records = store.list_all(limit=args.limit, offset=args.offset)
                    print(json.dumps([asdict(record) for record in records], ensure_ascii=False))

        buffered = stderr_buffer.getvalue()
        if buffered:
            print(buffered, file=sys.stderr, end="")
        return exit_code
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
