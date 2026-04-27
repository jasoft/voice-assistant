import sqlite3
import uuid

local_db = "data/voice_assistant_store.sqlite3"
remote_db = "data/remote_store.sqlite3"

def merge():
    l_conn = sqlite3.connect(local_db)
    r_conn = sqlite3.connect(remote_db)
    
    l_cur = l_conn.cursor()
    r_cur = r_conn.cursor()
    
    # 1. 获取本地已有的 ID 和 (memory, created_at) 组合，用于去重
    l_cur.execute("SELECT id, memory, created_at FROM remember_entries")
    local_data = l_cur.fetchall()
    local_ids = {row[0] for row in local_data}
    local_content_map = {(row[1], row[2]) for row in local_data}
    
    # 2. 从远程读取所有数据
    # 字段顺序: id, memory, original_text, created_at, updated_at, source_memory_id, user_id, photo_path
    fields = "id, memory, original_text, created_at, updated_at, source_memory_id, user_id, photo_path"
    r_cur.execute(f"SELECT {fields} FROM remember_entries")
    remote_rows = r_cur.fetchall()
    
    count_new = 0
    count_skipped = 0
    
    for row in remote_rows:
        rid, rmem, rorig, rc_at, ru_at, rsm_id, ruid, rphoto = row
        
        # 检查 ID 是否冲突
        if rid in local_ids:
            count_skipped += 1
            continue
            
        # 检查内容和时间是否完全一致（防止 ID 不同但内容重复）
        if (rmem, rc_at) in local_content_map:
            count_skipped += 1
            continue
            
        # 插入新数据
        l_cur.execute(
            f"INSERT INTO remember_entries ({fields}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, rmem, rorig, rc_at, ru_at, rsm_id, ruid, rphoto)
        )
        count_new += 1
        
    l_conn.commit()
    print(f"Merge Finished: {count_new} new records added, {count_skipped} duplicates skipped.")
    
    # 3. 同步 FTS 索引（如果本地用了 FTS）
    # 检查是否存在 FTS 表
    l_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='remember_entries_simple_fts'")
    if l_cur.fetchone():
        print("Updating FTS index...")
        # 简单粗暴的方法：把新插入的 ID 补进 FTS
        # 这里为了安全，我们只插入本地 FTS 里没有的记录
        l_cur.execute("""
            INSERT INTO remember_entries_simple_fts (memory, original_text, user_id, item_id)
            SELECT memory, original_text, user_id, id FROM remember_entries
            WHERE id NOT IN (SELECT item_id FROM remember_entries_simple_fts)
        """)
        l_conn.commit()
    
    l_conn.close()
    r_conn.close()

if __name__ == "__main__":
    merge()
