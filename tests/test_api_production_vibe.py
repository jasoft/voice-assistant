import os
import shutil
import subprocess
import time
import pytest
import requests
import json
from pathlib import Path

# 配置
API_PORT = 10055
API_URL = f"http://127.0.0.1:{API_PORT}/v1/query"
PROD_DB_PATH = Path("data/voice_assistant_store.sqlite3")
TEST_DB_PATH = Path("data/test_vibe_check.sqlite3")
TEST_USER_ID = "vibe_tester"

@pytest.fixture(scope="module", autouse=True)
def api_server():
    """启动测试 API 服务器"""
    # 1. 复制生产数据库
    if PROD_DB_PATH.exists():
        shutil.copy(PROD_DB_PATH, TEST_DB_PATH)
        print(f"Copied {PROD_DB_PATH} to {TEST_DB_PATH}")
    else:
        # 如果不存在，创建一个空的
        print(f"Warning: {PROD_DB_PATH} not found, using empty DB")
        TEST_DB_PATH.touch()

    # 1.5 插入测试 Token
    import sqlite3
    conn = sqlite3.connect(TEST_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO api_tokens (token, user_id, created_at) VALUES (?, ?, ?)", 
                   (TEST_USER_ID, TEST_USER_ID, "2026-04-27 00:00:00"))
    conn.commit()
    conn.close()

    # 2. 准备环境变量
    env = os.environ.copy()
    env["PTT_REMEMBER_DB_PATH"] = str(TEST_DB_PATH.absolute())
    env["PTT_HISTORY_DB_PATH"] = str(TEST_DB_PATH.absolute())
    env["PTT_USER_ID"] = TEST_USER_ID
    # 固定当前时间为 2026-04-27，以便相对日期查询可预测
    env["PTT_CURRENT_TIME"] = "2026-04-27 12:00:00"
    
    # 3. 启动服务器
    print(f"Starting ptt-api on port {API_PORT}...")
    proc = subprocess.Popen(
        ["ptt-api", "--port", str(API_PORT)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # 4. 等待就绪
    max_retries = 10
    ready = False
    for i in range(max_retries):
        try:
            # 尝试访问根目录或简单的健康检查（如果有的话）
            # 这里直接尝试访问这个端口
            time.sleep(1)
            # 简单的 socket 检测或请求
            requests.get(f"http://127.0.0.1:{API_PORT}/docs", timeout=1)
            ready = True
            break
        except requests.exceptions.RequestException:
            continue
    
    if not ready:
        proc.terminate()
        stdout, stderr = proc.communicate()
        print(f"STDOUT: {stdout}")
        print(f"STDERR: {stderr}")
        pytest.fail("API server failed to start")
        
    yield f"http://127.0.0.1:{API_PORT}"
    
    # 5. 清理
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    
    if TEST_DB_PATH.exists():
        os.remove(TEST_DB_PATH)

def call_api(query, mode="memory-chat"):
    headers = {"Authorization": f"Bearer {TEST_USER_ID}"}
    payload = {
        "query": query,
        "mode": mode
    }
    response = requests.post(API_URL, json=payload, headers=headers, timeout=60)
    assert response.status_code == 200, f"API failed: {response.text}"
    return response.json()

@pytest.mark.parametrize("scenario", [
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
    {"query": "2026年5月可能会有什么计划？", "desc": "未来展望（取决于记忆）"},
])
def test_vibe_scenarios(scenario):
    query = scenario["query"]
    print(f"\nTesting Scenario: {scenario['desc']} - Query: {query}")
    
    result = call_api(query)
    
    # 基础校验
    assert "reply" in result
    assert "memories" in result
    assert "query" in result
    assert "debug_info" in result
    
    print(f"Reply: {result['reply']}")
    print(f"Refined Query: {result['query']}")
    print(f"Memory count: {len(result['memories'])}")
    
    # 详细记录提取出的意图和日期
    debug = result.get("debug_info", {})
    intent_args = debug.get("query_args", {})
    if "start_date" in intent_args or "end_date" in intent_args:
        print(f"Extracted Date Range: {intent_args.get('start_date')} to {intent_args.get('end_date')}")
    if debug.get("intent"):
        print(f"Extracted Intent: {debug['intent'].get('intent')} via {debug['intent'].get('tool')}")
    
    # 简单的业务逻辑校验
    if "start_date" in str(result.get("debug_info", "")):
        print(f"Detected extracted date range in debug_info")
    
    # 对于 record 类的，检查 reply 是否包含“已记录”或类似确认
    if "帮我记" in query or "记一下" in query:
        # 这里取决于 LLM 的回复风格，但通常会有正面确认
        pass
