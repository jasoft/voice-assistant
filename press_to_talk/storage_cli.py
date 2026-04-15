import argparse
import json
import sys
from dataclasses import asdict
from press_to_talk.storage.service import StorageService, SessionHistoryRecord

def main() -> int:
    parser = argparse.ArgumentParser(prog="press-to-talk-storage")
    subparsers = parser.add_subparsers(dest="category", required=True)

    # History category
    history_parser = subparsers.add_parser("history")
    history_sub = history_parser.add_subparsers(dest="command", required=True)
    
    # history list
    h_list = history_sub.add_parser("list")
    h_list.add_argument("--limit", type=int, default=10)
    h_list.add_argument("--query", default="")

    # history add
    h_add = history_sub.add_parser("add")
    h_add.add_argument("--json", required=True, help="SessionHistoryRecord as JSON string")

    # history delete
    h_del = history_sub.add_parser("delete")
    h_del.add_argument("--session-id", required=True)

    # Memory category
    memory_parser = subparsers.add_parser("memory")
    memory_sub = memory_parser.add_subparsers(dest="command", required=True)

    # memory add
    m_add = memory_sub.add_parser("add")
    m_add.add_argument("--memory", required=True)
    m_add.add_argument("--original-text", default="")

    # memory search
    m_search = memory_sub.add_parser("search")
    m_search.add_argument("--query", required=True)

    # memory delete
    m_del = memory_sub.add_parser("delete")
    m_del.add_argument("--id", required=True)

    # memory list
    m_list = memory_sub.add_parser("list")
    m_list.add_argument("--limit", type=int, default=100)

    args = parser.parse_args()
    
    from press_to_talk.core import load_env_files
    load_env_files()
    
    try:
        service = StorageService.from_env(use_cli=False)
        # Force disable LLM features in CLI
        service.config.query_rewrite_enabled = False

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
