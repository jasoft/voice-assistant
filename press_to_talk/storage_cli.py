import argparse
import json
import sys
from dataclasses import asdict
from press_to_talk.storage.service import StorageService, SessionHistoryRecord

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="press-to-talk-storage",
        description="Standalone Storage CLI for session history and long-term memory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List recent 5 history records
  python -m press_to_talk.storage_cli history list --limit 5

  # Search history with a specific keyword
  python -m press_to_talk.storage_cli history list --query "passport"

  # Search memory entries directly using a keyword
  python -m press_to_talk.storage_cli memory search --query "ID number"

  # Save a memory entry with its source text
  python -m press_to_talk.storage_cli memory add --memory "Content to save" --original-text "Source transcript"

  # Delete a memory by its unique ID
  python -m press_to_talk.storage_cli memory delete --id <uuid>
""",
    )
    subparsers = parser.add_subparsers(dest="category", required=True)

    # History category
    history_parser = subparsers.add_parser("history", help="Manage session history records")
    history_sub = history_parser.add_subparsers(dest="command", required=True)
    
    # history list
    h_list = history_sub.add_parser("list", help="List recent history records as JSON")
    h_list.add_argument("--limit", type=int, default=10, help="Max records to return (default: 10)")
    h_list.add_argument("--query", default="", help="Fuzzy search transcript or reply")

    # history add
    h_add = history_sub.add_parser("add", help="Save a new history record")
    h_add.add_argument("--json", required=True, help="Full SessionHistoryRecord as a JSON string")

    # history delete
    h_del = history_sub.add_parser("delete", help="Delete a history record by session ID")
    h_del.add_argument("--session-id", required=True, help="The unique session_id to delete")

    # Memory category
    memory_parser = subparsers.add_parser("memory", help="Manage long-term memory entries")
    memory_sub = memory_parser.add_subparsers(dest="command", required=True)

    # memory add
    m_add = memory_sub.add_parser("add", help="Add a new long-term memory")
    m_add.add_argument("--memory", required=True, help="The extracted memory content")
    m_add.add_argument("--original-text", default="", help="The original transcript context")

    # memory search
    m_search = memory_sub.add_parser("search", help="Search memories using FTS5 (Pure storage search)")
    m_search.add_argument("--query", required=True, help="Search query (supports FTS5 syntax if available)")

    # memory delete
    m_del = memory_sub.add_parser("delete", help="Delete a memory entry by its ID")
    m_del.add_argument("--id", required=True, help="The unique memory ID (UUID)")

    # memory list
    m_list = memory_sub.add_parser("list", help="List all stored memories")
    m_list.add_argument("--limit", type=int, default=100, help="Max memories to list (default: 100)")

    args = parser.parse_args()
    
    from press_to_talk.core import load_env_files
    load_env_files()
    
    from press_to_talk.storage.service import load_storage_config
    
    try:
        config = load_storage_config()
        # Force disable LLM features in CLI BEFORE initializing the service
        config.query_rewrite_enabled = False
        service = StorageService(config, use_cli=False)

        if args.category == "history":
            store = service.history_store()
            if args.command == "list":
                records = store.list_recent(limit=args.limit, query=args.query)
                print(json.dumps([asdict(r) for r in records], ensure_ascii=False))
            elif args.command == "add":
                data = json.loads(args.json)
                record = SessionHistoryRecord(**data)
                store.persist(record)
                print(json.dumps({"status": "ok"}))
            elif args.command == "delete":
                store.delete(session_id=args.session_id)
                print(json.dumps({"deleted": args.session_id}))

        elif args.category == "memory":
            store = service.remember_store()
            if args.command == "add":
                res = store.add(memory=args.memory, original_text=args.original_text)
                print(json.dumps({"result": res}))
            elif args.command == "search":
                res = store.find(query=args.query)
                # If res is already a JSON string (as some stores return it), 
                # we should be careful not to double-encode it if it's supposed to be printed directly.
                # In the plan it says: print(res) # store.find already returns JSON string
                print(res)
            elif args.command == "delete":
                store.delete(memory_id=args.id)
                print(json.dumps({"deleted": args.id}))
            elif args.command == "list":
                records = store.list_all(limit=args.limit)
                print(json.dumps([asdict(r) for r in records], ensure_ascii=False))

        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
