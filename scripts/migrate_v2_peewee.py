import sys
import os
import shutil
import datetime
from pathlib import Path

# Add project root to sys.path
APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(APP_ROOT))

from press_to_talk.storage.models import db, APIToken, SessionHistory, RememberEntry
from press_to_talk.storage.service import DEFAULT_APP_DB_PATH

def migrate():
    db_path = DEFAULT_APP_DB_PATH.expanduser()
    if not db_path.exists():
        print(f"Database not found at {db_path}, skipping migration.")
        return

    # Backup the database before migration
    backup_path = db_path.parent / f"{db_path.name}.bak.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    print(f"Backing up database to {backup_path}...")
    shutil.copy2(db_path, backup_path)

    print(f"Migrating database at {db_path}...")
    db.init(str(db_path))
    db.connect()

    try:
        with db.atomic():
            # Ensure tables exist
            db.create_tables([APIToken, SessionHistory, RememberEntry])

            conn = db.connection()

            # Helper to add column if missing
            def add_column_if_missing(table_name, column_name, column_type="TEXT NOT NULL DEFAULT ''"):
                cursor = conn.execute(f"PRAGMA table_info({table_name})")
                columns = [row[1] for row in cursor.fetchall()]
                if column_name not in columns:
                    print(f"Adding {column_name} to {table_name}...")
                    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

            add_column_if_missing("session_histories", "user_id")
            add_column_if_missing("remember_entries", "user_id")

            # 1. Update session_histories
            updated_sessions = SessionHistory.update(user_id='soj').where(
                (SessionHistory.user_id == None) | (SessionHistory.user_id == '')
            ).execute()
            print(f"Updated {updated_sessions} session history records.")

            # 2. Update remember_entries
            updated_remember = RememberEntry.update(user_id='soj').where(
                (RememberEntry.user_id == None) | (RememberEntry.user_id == '')
            ).execute()
            print(f"Updated {updated_remember} remember entries.")

            # 3. Handle FTS and Embeddings tables (manual SQL)
            # Check remember_entries_simple_fts
            try:
                conn.execute("UPDATE remember_entries_simple_fts SET user_id = 'soj' WHERE user_id IS NULL OR user_id = ''")
                print("Updated FTS table.")
            except Exception as e:
                print(f"FTS table update skipped or failed: {e}")

            # Check remember_entry_embeddings
            try:
                # Check if user_id column exists
                cursor = conn.execute("PRAGMA table_info(remember_entry_embeddings)")
                info = cursor.fetchall()
                # Row format for PRAGMA table_info is (cid, name, type, notnull, dflt_value, pk)
                columns = [row[1] for row in info]
                if "user_id" not in columns:
                    conn.execute("ALTER TABLE remember_entry_embeddings ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
                    print("Added user_id column to embeddings table.")
                
                conn.execute("UPDATE remember_entry_embeddings SET user_id = 'soj' WHERE user_id IS NULL OR user_id = ''")
                print("Updated embeddings table.")
            except Exception as e:
                print(f"Embeddings table update skipped or failed: {e}")

            # 4. Create default API token
            token_val = 'soj-default-token'
            if not APIToken.select().where(APIToken.token == token_val).exists():
                APIToken.create(
                    token=token_val,
                    user_id='soj',
                    description='Default token for soj'
                )
                print(f"Created default API token: {token_val}")
            else:
                print(f"API token {token_val} already exists.")
    except Exception as e:
        print(f"Migration failed: {e}")
        raise
    finally:
        if not db.is_closed():
            db.close()
    print("Migration completed successfully.")

if __name__ == "__main__":
    migrate()
