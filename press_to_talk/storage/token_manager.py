from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

from .models import APIToken, db
from .service import load_storage_config


def init_db():
    config = load_storage_config()
    db_path = config.history_db_path
    if not db_path:
        from .service import DEFAULT_HISTORY_DB_PATH
        db_path = str(DEFAULT_HISTORY_DB_PATH)
    
    db.init(db_path)
    db.connect(reuse_if_open=True)
    db.create_tables([APIToken])


def create_token(user_id: str, token: str | None = None, description: str | None = None):
    if not token:
        token = secrets.token_urlsafe(24)
    
    try:
        APIToken.create(
            token=token,
            user_id=user_id,
            description=description
        )
        print(f"✅ Created token for user '{user_id}':")
        print(f"   Token: {token}")
        if description:
            print(f"   Description: {description}")
    except Exception as e:
        print(f"❌ Failed to create token: {e}")


def list_tokens():
    tokens = APIToken.select().order_by(APIToken.created_at.desc())
    if not tokens:
        print("No tokens found.")
        return

    print(f"{'User ID':<15} {'Token':<35} {'Created At':<25} {'Description'}")
    print("-" * 100)
    for t in tokens:
        desc = t.description or ""
        print(f"{t.user_id:<15} {t.token:<35} {str(t.created_at):<25} {desc}")


def delete_token(token: str):
    q = APIToken.delete().where(APIToken.token == token)
    count = q.execute()
    if count > 0:
        print(f"✅ Deleted token: {token}")
    else:
        print(f"❌ Token not found: {token}")


def main():
    parser = argparse.ArgumentParser(description="Manage API tokens for Press-to-Talk.")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Add
    add_parser = subparsers.add_parser("add", help="Add a new token")
    add_parser.add_argument("user_id", help="User ID associated with the token")
    add_parser.add_argument("--token", help="Specific token string (optional, will be generated if omitted)")
    add_parser.add_argument("--desc", help="Description for the token")

    # List
    subparsers.add_parser("list", help="List all tokens")

    # Delete
    del_parser = subparsers.add_parser("delete", help="Delete a token")
    del_parser.add_argument("token", help="The token string to delete")

    args = parser.parse_args()

    init_db()

    if args.command == "add":
        create_token(args.user_id, args.token, args.desc)
    elif args.command == "list":
        list_tokens()
    elif args.command == "delete":
        delete_token(args.token)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
