
import os
import sqlite3
import uuid
import json
from pathlib import Path
from press_to_talk.storage.providers.sqlite_fts import SQLiteFTS5RememberStore
from press_to_talk.storage.models import RememberEntry, db

def setup_db(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init(db_path)
    # Force table name to match what SQLiteFTS5RememberStore expects
    RememberEntry._meta.table_name = "remember_entries"
    db.create_tables([RememberEntry])
    db.close()

def get_native_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def test_isolation_leak():
    db_path = "test_isolation.db"
    setup_db(db_path)
    
    user1 = "user_1"
    user2 = "user_2"
    
    # Store instances will use db.connection() which might be the same global db
    store1 = SQLiteFTS5RememberStore(user_id=user1, db_path=db_path)
    store2 = SQLiteFTS5RememberStore(user_id=user2, db_path=db_path)
    
    # 1. User 1 adds a memory
    store1.add(memory="Secret of User 1", original_text="U1 content")
    
    # Extract ID from DB using native conn to avoid Peewee connection issues
    with get_native_conn(db_path) as conn:
        row = conn.execute("SELECT id FROM remember_entries WHERE user_id = ?", (user1,)).fetchone()
        mem1_id = row["id"]
    
    print(f"User 1 added memory: {mem1_id}")
    
    # 2. Test list_all leak
    all_memories_for_u2 = store2.list_all()
    leaked_list = [m for m in all_memories_for_u2 if m.id == mem1_id]
    if leaked_list:
        print("❌ LEAK FOUND: User 2 can list User 1's memory via list_all()")
    else:
        print("✅ list_all() isolation OK")
        
    # 3. Test update leak
    try:
        store2.update(memory_id=mem1_id, memory="Hacked by User 2")
        # Check if actually updated
        with get_native_conn(db_path) as conn:
            row = conn.execute("SELECT memory FROM remember_entries WHERE id = ?", (mem1_id,)).fetchone()
            if row and row["memory"] == "Hacked by User 2":
                print("❌ LEAK FOUND: User 2 can update User 1's memory!")
            else:
                print("✅ update() isolation OK (unexpectedly?)")
    except Exception as e:
        print(f"✅ update() isolation OK: {e}")

    # 4. Test delete leak
    store2.delete(memory_id=mem1_id)
    with get_native_conn(db_path) as conn:
        row = conn.execute("SELECT 1 FROM remember_entries WHERE id = ?", (mem1_id,)).fetchone()
        if row is None:
            print("❌ LEAK FOUND: User 2 can delete User 1's memory!")
        else:
            print("✅ delete() isolation OK")

    if os.path.exists(db_path):
        os.remove(db_path)

if __name__ == "__main__":
    test_isolation_leak()
