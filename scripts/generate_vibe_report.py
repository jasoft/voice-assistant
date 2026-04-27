import datetime
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests

# --- Configuration ---
API_PORT = 10066
API_URL = f"http://127.0.0.1:{API_PORT}/v1/query"
PROD_DB_PATH = Path("data/voice_assistant_store.sqlite3")
TEST_DB_PATH = Path("data/report_test.sqlite3")
TEST_USER_ID = "report_tester"
REPORT_FILE = Path("data/vibe_report.html")

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
        print(
            f"Warning: Production DB {PROD_DB_PATH} not found, starting with empty DB"
        )
        TEST_DB_PATH.touch()

    import sqlite3

    conn = sqlite3.connect(TEST_DB_PATH)
    cursor = conn.cursor()

    # 1. 强制将所有存量记忆改为测试用户，确保召回
    print(f"Reassigning all records in {TEST_DB_PATH} to {TEST_USER_ID}...")
    cursor.execute("UPDATE remember_entries SET user_id = ?", (TEST_USER_ID,))

    # 尝试更新嵌入表
    try:
        cursor.execute(
            "UPDATE remember_entry_embeddings SET user_id = ?", (TEST_USER_ID,)
        )
    except sqlite3.OperationalError:
        pass  # 可能表还没创建

    # 尝试更新 FTS 表 (如果存在且有 user_id 字段)
    try:
        cursor.execute(
            "UPDATE remember_entries_simple_fts SET user_id = ?", (TEST_USER_ID,)
        )
    except sqlite3.OperationalError:
        pass

    # 2. 插入测试 Token
    cursor.execute(
        "INSERT OR REPLACE INTO api_tokens (token, user_id, created_at) VALUES (?, ?, ?)",
        (TEST_USER_ID, TEST_USER_ID, "2026-04-27 00:00:00"),
    )

    conn.commit()
    conn.close()


def generate_html(results):
    html_template = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PTT API Vibe Check Report</title>
    <style>
        :root {
            --primary: #2563eb;
            --bg: #f8fafc;
            --card: #ffffff;
            --text: #1e293b;
            --accent: #64748b;
            --success: #10b981;
            --warning: #f59e0b;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            line-height: 1.6;
            margin: 0;
            padding: 40px 20px;
        }
        .container {
            max-width: 1000px;
            margin: 0 auto;
        }
        header {
            text-align: center;
            margin-bottom: 40px;
        }
        h1 {
            color: var(--primary);
            font-size: 2.5rem;
            margin-bottom: 10px;
        }
        .meta {
            color: var(--accent);
            font-size: 1rem;
        }
        .card {
            background: var(--card);
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            margin-bottom: 24px;
            padding: 24px;
            border: 1px solid #e2e8f0;
            transition: transform 0.2s;
        }
        .card:hover {
            transform: translateY(-2px);
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 16px;
            border-bottom: 1px solid #f1f5f9;
            padding-bottom: 12px;
        }
        .query-title {
            font-weight: 700;
            font-size: 1.25rem;
            color: var(--primary);
        }
        .badge {
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }
        .badge-intent { background: #dbeafe; color: #1e40af; }
        .badge-date { background: #fef3c7; color: #92400e; }
        .badge-success { background: #d1fae5; color: #065f46; }

        .section-title {
            font-size: 0.875rem;
            font-weight: 600;
            color: var(--accent);
            text-transform: uppercase;
            margin-top: 16px;
            margin-bottom: 8px;
        }
        .reply-box {
            background: #f1f5f9;
            padding: 16px;
            border-radius: 8px;
            white-space: pre-wrap;
            font-size: 0.95rem;
        }
        .memories-list {
            list-style: none;
            padding: 0;
        }
        .memory-item {
            font-size: 0.85rem;
            padding: 8px;
            border-left: 3px solid var(--primary);
            background: #f8fafc;
            margin-bottom: 4px;
            display: flex;
            justify-content: space-between;
        }
        .score { font-weight: 700; color: var(--success); }
        footer {
            text-align: center;
            margin-top: 60px;
            color: var(--accent);
            font-size: 0.875rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Press-to-Talk Vibe Check</h1>
            <div class="meta">报告生成时间: CURRENT_TIME | 基准模拟时间: 2026-04-27</div>
        </header>

        RESULTS_HTML

        <footer>
            &copy; 2026 Voice Assistant Project - Created by Gemini CLI
        </footer>
    </div>
</body>
</html>
"""
    results_html = ""
    for r in results:
        debug = r.get("debug_info", {})
        intent = debug.get("intent", {}).get("intent", "unknown")
        tool = debug.get("intent", {}).get("tool", "")
        args = debug.get("query_args", {})
        date_range = (
            f"{args.get('start_date', '')} 至 {args.get('end_date', '')}"
            if args.get("start_date")
            else "全时段"
        )

        memories_html = ""
        for m in r.get("memories", []):
            memories_html += f"""
            <div class="memory-item">
                <span>{m["memory"]}</span>
                <span class="score">{m.get("score", 0):.2f}</span>
            </div>
            """
        if not memories_html:
            memories_html = (
                "<p style='font-size: 0.8rem; color: #94a3b8;'>没有命中的原始记忆</p>"
            )

        results_html += f"""
        <div class="card">
            <div class="card-header">
                <div>
                    <div class="query-title">{r["user_query"]}</div>
                    <div style="font-size: 0.8rem; color: #64748b; margin-top: 4px;">{r["desc"]}</div>
                </div>
                <div style="display: flex; gap: 8px;">
                    <span class="badge badge-intent">{intent} ({tool})</span>
                    <span class="badge badge-date">{date_range}</span>
                </div>
            </div>

            <div class="section-title">意图提取结果 (Refined Query)</div>
            <div style="font-weight: 600; color: #334155; margin-bottom: 12px;">{r["refined_query"]}</div>

            <div class="section-title">助手回复</div>
            <div class="reply-box">{r["reply"]}</div>

            <div class="section-title">检索到的记忆背景 (Top 5)</div>
            <div class="memories-list">
                {memories_html}
            </div>
        </div>
        """

    final_html = html_template.replace("RESULTS_HTML", results_html)
    final_html = final_html.replace(
        "CURRENT_TIME", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    with REPORT_FILE.open("w", encoding="utf-8") as f:
        f.write(final_html)
    print(f"Report generated: {REPORT_FILE.absolute()}")


def main():
    print("Starting Report Generation...")
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
    )

    # Wait for API
    time.sleep(3)

    results = []
    headers = {"Authorization": f"Bearer {TEST_USER_ID}"}

    for i, scenario in enumerate(SCENARIOS):
        print(f"[{i + 1}/{len(SCENARIOS)}] Testing: {scenario['query']}")
        try:
            resp = requests.post(
                API_URL,
                json={"query": scenario["query"], "mode": "memory-chat"},
                headers=headers,
                timeout=60,
            )
            data = resp.json()
            results.append(
                {
                    "user_query": scenario["query"],
                    "desc": scenario["desc"],
                    "refined_query": data.get("query", ""),
                    "reply": data.get("reply", ""),
                    "memories": data.get("memories", [])[:5],
                    "debug_info": data.get("debug_info", {}),
                }
            )
        except Exception as e:
            print(f"Error testing {scenario['query']}: {e}")

    proc.terminate()
    generate_html(results)

    if TEST_DB_PATH.exists():
        os.remove(TEST_DB_PATH)

    # Start server
    print("\nStarting Web Server to show report...")
    # Background server
    server_proc = subprocess.Popen(
        ["python3", "-m", "http.server", "10077", "--directory", "data"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    url = "http://127.0.0.1:10077/vibe_report.html"
    print(f"\nReport is live at: {url}")

    # Open browser
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
