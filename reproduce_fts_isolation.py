import os
import sqlite3
import contextlib
from pathlib import Path
from press_to_talk.storage.service import StorageService, StorageConfig
from press_to_talk.storage.models import db, RememberEntry, APIToken, SessionHistory

def test_fts_isolation():
    db_path = "test_isolation.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    
    # Initialize once
    db.init(db_path)
    db.connect()
    db.create_tables([APIToken, SessionHistory, RememberEntry])
    
    config_a = StorageConfig(backend="sqlite_fts5", user_id="user_a", remember_db_path=db_path, history_db_path=db_path)
    # We pass use_cli=False to use Peewee
    service_a = StorageService(config_a, use_cli=False)
    store_a = service_a.remember_store()
    
    config_b = StorageConfig(backend="sqlite_fts5", user_id="user_b", remember_db_path=db_path, history_db_path=db_path)
    service_b = StorageService(config_b, use_cli=False)
    store_b = service_b.remember_store()
    
    print("--- Adding memory for User A ---")
    store_a.add(memory="User A likes apples")
    
    print("--- Verifying User A can find it ---")
    res_a = store_a.find(query="apples")
    print(f"User A find 'apples': {res_a}")
    assert "apples" in res_a
    
    print("--- Adding memory for User B ---")
    store_b.add(memory="User B likes bananas")
    
    print("--- Verifying User B can find it ---")
    res_b = store_b.find(query="bananas")
    print(f"User B find 'bananas': {res_b}")
    assert "bananas" in res_b
    
    print("--- Checking if User A can still find their memory ---")
    # This is expected to FAIL if my theory is correct, because User B's _connect wiped the FTS table
    res_a_retry = store_a.find(query="apples")
    print(f"User A find 'apples' again: {res_a_retry}")
    
    if "apples" not in res_a_retry:
        print("CRITICAL FAILURE: User A's memory disappeared from FTS after User B performed an operation!")
    else:
        print("SUCCESS: User A's memory is still there (isolation works).")

if __name__ == "__main__":
    try:
        test_fts_isolation()
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        if os.path.exists("test_isolation.db"):
             # Close connection before removing
             db.close()
             os.remove("test_isolation.db")
