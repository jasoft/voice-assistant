import os
import json
from press_to_talk.storage.providers.sqlite_fts import SQLiteFTS5RememberStore
from press_to_talk.storage.service import load_storage_config
from press_to_talk.utils.env import load_env_files

def test_find_fail():
    load_env_files()
    config = load_storage_config()
    store = SQLiteFTS5RememberStore.from_config(config)
    
    query = "最近三天的记录"
    start_date = "2026-04-24"
    end_date = "2026-04-27"
    
    print(f"Testing find with query: {query}, date: {start_date} to {end_date}")
    try:
        res = store.find(query=query, start_date=start_date, end_date=end_date)
        print("Result length:", len(json.loads(res)["results"]))
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_find_fail()
