import os
import shutil
import sqlite3
from pathlib import Path
from press_to_talk.storage.providers.sqlite_fts import SQLiteFTS5RememberStore
from press_to_talk.storage.models import db, RememberEntry, SessionHistory, APIToken

# Setup test DB
DB_PATH = Path("tmp/test_isolation.db")
if DB_PATH.exists():
    os.remove(DB_PATH)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

db.init(str(DB_PATH))
db.connect()
db.create_tables([RememberEntry, SessionHistory, APIToken])

def test_fts_isolation():
    store_a = SQLiteFTS5RememberStore(user_id="user_a", db_path=DB_PATH)
    store_b = SQLiteFTS5RememberStore(user_id="user_b", db_path=DB_PATH)

    # Add memories
    print("Adding memory for user_a...")
    store_a.add(memory="I love apples", original_text="apples are great")
    
    print("Adding memory for user_b...")
    store_b.add(memory="I love oranges", original_text="oranges are juicy")

    # Search for apples as user_a
    print("Searching for 'apples' as user_a...")
    res_a = store_a.find(query="apples")
    print(f"User A results: {res_a}")
    assert "apples" in res_a
    assert "oranges" not in res_a

    # Search for apples as user_b
    print("Searching for 'apples' as user_b...")
    res_b = store_b.find(query="apples")
    print(f"User B results: {res_b}")
    assert "apples" not in res_b

    # Search for oranges as user_b
    print("Searching for 'oranges' as user_b...")
    res_b2 = store_b.find(query="oranges")
    print(f"User B results (oranges): {res_b2}")
    assert "oranges" in res_b2
    assert "apples" not in res_b2

    # Verify FTS table structure
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM remember_entries_simple_fts")
    rows = cursor.fetchall()
    print(f"FTS Table rows: {rows}")
    # Column 0: memory, 1: original_text, 2: user_id, 3: item_id
    for row in rows:
        assert len(row) == 4
        if "apples" in row[0]:
            assert row[2] == "user_a"
        if "oranges" in row[0]:
            assert row[2] == "user_b"
    conn.close()

    print("Isolation test PASSED!")

if __name__ == "__main__":
    try:
        test_fts_isolation()
    finally:
        db.close()
        if DB_PATH.exists():
            os.remove(DB_PATH)
