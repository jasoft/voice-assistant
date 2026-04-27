import os
import shutil
import subprocess
import time
import requests
import json
from pathlib import Path
import datetime

# --- Configuration ---
SKILL_ROOT = Path(__file__).resolve().parents[1]
API_PORT = 10066
API_URL = f"http://127.0.0.1:{API_PORT}/v1/query"
PROD_DB_PATH = Path("data/voice_assistant_store.sqlite3")
TEST_DB_PATH = Path("data/report_test.sqlite3")
TEST_USER_ID = "report_tester"
REPORT_FILE = Path("data/vibe_report.html")
TEMPLATE_FILE = SKILL_ROOT / "assets" / "report_template.html"

SCENARIOS = [
    {"query": "最近三天的记忆", "desc": "相对日期提取"},
    {"query": "上周的记录", "desc": "相对日期提取 (上周)"},
    {"query": "昨天我说了什么", "desc": "昨天日期提取"},
    {"query": "查询关于壮壮的记忆", "desc": "关键词搜索 (壮壮)"},
    {"query": "伊朗什么时候停火的？", "desc": "事实查询"},
    {"query": "护照在哪里？", "desc": "位置查询"},
    {"query": "验证码是多少？", "desc": "敏感信息查询"},
    {"query": "帮我记一下，今天中午吃的是火锅", "desc": "记录意图 (record)"},
    {"query": "今天我吃什么了？", "desc": "即时召回"},
    {"query": "钥匙在玄关柜子上", "desc": "隐式记录判定"},
    {"query": "钥匙在哪？", "desc": "位置查询 (记录后)"},
    {"query": "2026年4月15日的记录", "desc": "特定日期查询"},
    {"query": "4月11日到4月14日的记录", "desc": "日期范围查询"},
    {"query": "帮我总结一下最近三天的生活", "desc": "总结性查询"},
    {"query": "早上好", "desc": "寒暄/默认查找"},
    {"query": "帮我记一下：明天下午三点开会", "desc": "含未来时间的记录"},
    {"query": "刚才我们说了什么？", "desc": "会话历史查询"},
    {"query": "壮壮上周干了什么？", "desc": "关键词+日期范围"},
    {"query": "我最喜欢的电脑是什么？", "desc": "个人偏好查询"},
    {"query": "上周关于工作的记录", "desc": "关键词+日期范围 (工作)"},
    {"query": "2026年5月可能会有什么计划？", "desc": "未来展望"},
]

def setup_test_db():
    if PROD_DB_PATH.exists():
        shutil.copy(PROD_DB_PATH, TEST_DB_PATH)
        print(f"Copied production DB to {TEST_DB_PATH}")
    else:
        print(f"Warning: Production DB {PROD_DB_PATH} not found, starting with empty DB")
        TEST_DB_PATH.touch()
    
    import sqlite3
    conn = sqlite3.connect(TEST_DB_PATH)
    cursor = conn.cursor()
    
    # 强制将所有存量记忆改为测试用户，确保召回
    print(f"Reassigning all records to {TEST_USER_ID}...")
    try:
        cursor.execute("UPDATE remember_entries SET user_id = ?", (TEST_USER_ID,))
        cursor.execute("UPDATE remember_entry_embeddings SET user_id = ?", (TEST_USER_ID,))
        cursor.execute("UPDATE remember_entries_simple_fts SET user_id = ?", (TEST_USER_ID,))
    except Exception:
        pass
        
    cursor.execute("INSERT OR REPLACE INTO api_tokens (token, user_id, created_at) VALUES (?, ?, ?)", 
                   (TEST_USER_ID, TEST_USER_ID, "2026-04-27 00:00:00"))
    conn.commit()
    conn.close()

def generate_html(results):
    if not TEMPLATE_FILE.exists():
        print(f"Error: Template file not found at {TEMPLATE_FILE}")
        return

    with TEMPLATE_FILE.open("r", encoding="utf-8") as f:
        template = f.read()

    # 关键修复：使用 ensure_ascii=True 确保生成的 HTML 字符完全由 ASCII 组成
    # 这样即便 HTTP Server 或浏览器编码猜测错误，中文也能正常显示
    json_data = json.dumps(results, ensure_ascii=True)
    
    final_html = template.replace("{{results_json}}", json_data)
    final_html = final_html.replace("{{current_time}}", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_FILE.open("w", encoding="utf-8") as f:
        f.write(final_html)
    print(f"Report generated: {REPORT_FILE.absolute()}")

def main():
    print("Starting Vibe Check (Safe & Stable Mode)...")
    setup_test_db()
    
    env = os.environ.copy()
    env["PTT_REMEMBER_DB_PATH"] = str(TEST_DB_PATH.absolute())
    env["PTT_HISTORY_DB_PATH"] = str(TEST_DB_PATH.absolute())
    env["PTT_USER_ID"] = TEST_USER_ID
    env["PTT_CURRENT_TIME"] = "2026-04-27 12:00:00"
    
    print(f"Launching API on port {API_PORT}...")
    proc = subprocess.Popen(
        ["uv", "run", "ptt-api", "--port", str(API_PORT)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # Wait for API to warm up
    time.sleep(8)
    
    results = []
    headers = {"Authorization": f"Bearer {TEST_USER_ID}"}
    
    # 改为串行执行，确保稳定性，增加超时到 180s
    for i, scenario in enumerate(SCENARIOS):
        print(f"[{i+1}/{len(SCENARIOS)}] Testing: {scenario['query']}...", end="", flush=True)
        try:
            start_t = time.time()
            resp = requests.post(API_URL, json={
                "query": scenario["query"],
                "mode": "memory-chat"
            }, headers=headers, timeout=180)
            
            if resp.status_code == 200:
                data = resp.json()
                results.append({
                    "user_query": scenario["query"],
                    "desc": scenario["desc"],
                    "refined_query": data.get("query", ""),
                    "reply": data.get("reply", ""),
                    "memories": data.get("memories", [])[:5],
                    "debug_info": data.get("debug_info", {})
                })
                print(f" OK ({time.time()-start_t:.1f}s)")
            else:
                print(f" FAILED (Status {resp.status_code})")
                results.append({
                    "user_query": scenario["query"],
                    "desc": scenario["desc"],
                    "refined_query": "ERROR",
                    "reply": f"API 返回错误: {resp.text}",
                    "memories": [],
                    "debug_info": {}
                })
        except Exception as e:
            print(f" TIMEOUT/ERROR: {e}")
            results.append({
                "user_query": scenario["query"],
                "desc": scenario["desc"],
                "refined_query": "ERROR",
                "reply": f"请求异常: {e}",
                "memories": [],
                "debug_info": {}
            })

    proc.terminate()
    generate_html(results)
    
    if TEST_DB_PATH.exists():
        os.remove(TEST_DB_PATH)
    
    print("\nStarting Web Server on port 10077...")
    # 强制让 http.server 支持 .html 的 utf-8
    server_proc = subprocess.Popen(
        ["python3", "-m", "http.server", "10077", "--directory", "data"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    url = "http://127.0.0.1:10077/vibe_report.html"
    print(f"\nReport is live at: {url}")
    
    try:
        subprocess.run(["open", url])
    except:
        pass
    
    print("\nPress Ctrl+C to stop the server.")
    try:
        server_proc.wait()
    except KeyboardInterrupt:
        server_proc.terminate()

if __name__ == "__main__":
    main()
