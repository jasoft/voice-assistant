import sqlite3
import httpx
import os

DB_PATH = "data/voice_assistant_store.sqlite3"
PB_URL = "http://127.0.0.1:18090/api/collections/api_tokens/records"

def migrate_tokens():
    if not os.path.exists(DB_PATH):
        print("DB not found")
        return
        
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM api_tokens").fetchall()
    
    client = httpx.Client()
    count = 0
    for r in rows:
        data = {
            "token": r["token"],
            "user_id": r["user_id"],
            "description": r["description"] or ""
        }
        res = client.post(PB_URL, json=data)
        if res.status_code in (200, 201):
            count += 1
        else:
            print(f"Failed to import token: {res.text}")
    
    print(f"Migrated {count} tokens.")
    conn.close()

if __name__ == "__main__":
    migrate_tokens()
