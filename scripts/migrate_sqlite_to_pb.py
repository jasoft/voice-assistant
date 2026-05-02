import sqlite3
import httpx
import os
import json

DB_PATH = "data/voice_assistant_store.sqlite3"
PB_BASE_URL = "http://127.0.0.1:18090/api"
ADMIN_EMAIL = "migration@local.com"
ADMIN_PASS = "migration123456"

def get_admin_token():
    res = httpx.post(f"{PB_BASE_URL}/admins/auth-with-password", json={
        "identity": ADMIN_EMAIL,
        "password": ADMIN_PASS
    })
    res.raise_for_status()
    return res.json()["token"]

def clear_collection(client, collection, headers):
    print(f"Clearing {collection}...")
    while True:
        res = client.get(f"{PB_BASE_URL}/collections/{collection}/records", headers=headers, params={"perPage": 500})
        items = res.json().get("items", [])
        if not items:
            break
        for item in items:
            client.delete(f"{PB_BASE_URL}/collections/{collection}/records/{item['id']}", headers=headers)

def migrate():
    token = get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    client = httpx.Client(timeout=60.0)

    # 1. Clear existing
    clear_collection(client, "remember_entries", headers)
    clear_collection(client, "session_histories", headers)
    clear_collection(client, "users", headers)
    clear_collection(client, "api_tokens", headers)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 2. Users
    print("Migrating users...")
    cursor.execute("SELECT * FROM users")
    for row in cursor.fetchall():
        data = {
            "user_id": row["user_id"],
            "nickname": row["nickname"] or "",
            "system_prompt": row["system_prompt"] or "",
            "preferences": row["preferences"] or "{}",
            "username": row["user_id"].replace("/", "_")[:15],
            "password": "Pb_Password_123",
            "passwordConfirm": "Pb_Password_123",
            "created": row["created_at"]
        }
        client.post(f"{PB_BASE_URL}/collections/users/records", json=data, headers=headers)

    # 3. API Tokens
    print("Migrating api_tokens...")
    cursor.execute("SELECT * FROM api_tokens")
    for row in cursor.fetchall():
        data = {
            "token": row["token"],
            "user_id": row["user_id"],
            "description": row["description"] or "",
            "created": row["created_at"]
        }
        client.post(f"{PB_BASE_URL}/collections/api_tokens/records", json=data, headers=headers)

    # 4. Remember Entries
    print("Migrating remember_entries...")
    cursor.execute("SELECT * FROM remember_entries")
    for row in cursor.fetchall():
        data = {
            "user_id": row["user_id"] or "default",
            "memory": row["memory"],
            "original_text": row["original_text"] or "",
            "photo_path": row["photo_path"] or "",
            "source_memory_id": row["source_memory_id"] or "",
        }
        res = client.post(f"{PB_BASE_URL}/collections/remember_entries/records", json=data, headers=headers)
        if res.status_code in (200, 201):
            rid = res.json()["id"]
            # Try to force created/updated via PATCH
            client.patch(f"{PB_BASE_URL}/collections/remember_entries/records/{rid}", json={
                "created": f"{row['created_at']}.000Z",
                "updated": f"{row['updated_at']}.000Z"
            }, headers=headers)

    # 5. Session Histories
    print("Migrating session_histories...")
    cursor.execute("SELECT * FROM session_histories")
    for row in cursor.fetchall():
        data = {
            "session_id": row["session_id"],
            "user_id": row["user_id"] or "default",
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "transcript": row["transcript"],
            "reply": row["reply"],
            "peak_level": row["peak_level"],
            "mean_level": row["mean_level"],
            "auto_closed": bool(row["auto_closed"]),
            "reopened_by_click": bool(row["reopened_by_click"]),
            "mode": row["mode"],
        }
        res = client.post(f"{PB_BASE_URL}/collections/session_histories/records", json=data, headers=headers)
        if res.status_code in (200, 201):
            rid = res.json()["id"]
            client.patch(f"{PB_BASE_URL}/collections/session_histories/records/{rid}", json={
                "created": f"{row['created_at']}.000Z"
            }, headers=headers)


    print("Migration complete with timestamps preserved.")
    conn.close()

if __name__ == "__main__":
    migrate()
