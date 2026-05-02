import json
import os
import httpx
from press_to_talk.storage.service import load_storage_config, StorageService

def main():
    print("🚀 Starting all-user embedding rebuild...")
    
    # 加载配置
    config = load_storage_config(user_id_override="admin")
    service = StorageService(config, use_cli=False)
    store = service.remember_store()
    
    if not store._embedding_enabled():
        print("❌ Embedding is not enabled in config. Check your workflow_config.json.")
        return

    pb_url = os.environ.get("PTT_PB_URL", "http://127.0.0.1:18090").rstrip("/")
    api_url = f"{pb_url}/api"
    
    with httpx.Client() as client:
        page = 1
        total_rebuilt = 0
        total_items = 0
        
        while True:
            res = client.get(f"{api_url}/collections/remember_entries/records", params={"page": page, "perPage": 50})
            res.raise_for_status()
            data = res.json()
            items = data.get("items", [])
            if not items:
                break
                
            for item in items:
                total_items += 1
                if not item.get("embedding_json"):
                    print(f"  - [{total_items}] Rebuilding: {item['id']} (User: {item['user_id']})")
                    # 临时切换 user_id 以匹配记录
                    store.user_id = item["user_id"]
                    if store._sync_record_embedding(item):
                        total_rebuilt += 1
                
            if page >= data.get("totalPages", 1):
                break
            page += 1
            
        print(f"\n✨ Done! Scanned {total_items} items, rebuilt {total_rebuilt} missing embeddings.")

if __name__ == "__main__":
    main()
