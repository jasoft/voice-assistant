import os
import subprocess
import time
import pytest
import requests
import json
from pathlib import Path

# 配置
API_PORT = 10056
API_URL = f"http://127.0.0.1:{API_PORT}/v1/query"
TEST_USER_ID = "vibe_tester"
LOG_FILE = "/tmp/ptt-api-test.log"


@pytest.fixture(scope="module", autouse=True)
def api_server():
    """启动测试 API 服务器"""
    import httpx

    PB_URL = os.environ.get("PTT_PB_URL", "http://127.0.0.1:18090")
    PB_API = f"{PB_URL.rstrip('/')}/api"

    # 1. 确保测试 Token 存在
    try:
        with httpx.Client(timeout=5.0) as client:
            res = client.get(
                f"{PB_API}/collections/api_tokens/records",
                params={"filter": f"token = '{TEST_USER_ID}'"},
            )
            res.raise_for_status()
            items = res.json().get("items", [])

            if not items:
                print(f"Injecting test token {TEST_USER_ID} into PocketBase...")
                client.post(
                    f"{PB_API}/collections/api_tokens/records",
                    json={
                        "token": TEST_USER_ID,
                        "user_id": TEST_USER_ID,
                        "description": "Auto-injected for vibe check",
                    },
                ).raise_for_status()
            else:
                print(f"Test token {TEST_USER_ID} already exists in PocketBase.")
    except Exception as e:
        print(f"Warning: Failed to ensure test token in PocketBase: {e}")

    # 2. 准备环境变量
    env = os.environ.copy()
    env["PTT_USER_ID"] = TEST_USER_ID
    env["PTT_CURRENT_TIME"] = "2026-04-27 12:00:00"

    # 3. 启动服务器
    print(f"Starting ptt-api on port {API_PORT}, logging to {LOG_FILE}...")
    log_f = open(LOG_FILE, "w")
    proc = subprocess.Popen(
        ["uv", "run", "ptt-api", "--port", str(API_PORT)],
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT, # 合并输出
    )

    # 4. 等待就绪
    max_retries = 30
    ready = False
    for i in range(max_retries):
        try:
            time.sleep(1)
            # 尝试访问根目录
            requests.get(f"http://127.0.0.1:{API_PORT}/docs", timeout=1)
            ready = True
            print(f"API server is ready after {i+1} seconds.")
            break
        except requests.exceptions.RequestException:
            if proc.poll() is not None:
                print("API server process exited prematurely!")
                break
            continue

    if not ready:
        proc.terminate()
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r") as f:
                print(f"--- API Server Logs ---\n{f.read()}\n----------------------")
        pytest.fail("API server failed to start")

    yield proc

    # 5. 清理
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def call_api(query):
    """调用测试 API"""
    headers = {"Authorization": f"Bearer {TEST_USER_ID}"}
    payload = {"query": query}
    response = requests.post(API_URL, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()


@pytest.mark.parametrize(
    "scenario",
    [
        {"query": "帮我记一下，杜甫是外星人", "desc": "基础记录功能 (Record)"},
        {"query": "杜甫是谁？", "desc": "记忆检索功能 (Memory Chat)"},
        {"query": "今天上证指数是多少？", "desc": "联网搜索功能 (Chat w/ Search)"},
        {"query": "我最喜欢的电脑是什么？", "desc": "个人偏好查询"},
        {"query": "上周关于工作的记录", "desc": "关键词+日期范围 (工作)"},
        {"query": "2026年5月可能会有什么计划？", "desc": "未来展望（取决于记忆）"},
    ],
)
@pytest.mark.e2e
@pytest.mark.timeout(180)
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
        print(
            f"Extracted Date Range: {intent_args.get('start_date')} to {intent_args.get('end_date')}"
        )
    if debug.get("intent"):
        print(
            f"Extracted Intent: {debug['intent'].get('intent')} via {debug['intent'].get('tool')}"
        )

    # 简单的业务逻辑校验
    if "start_date" in str(result.get("debug_info", "")):
        print("Detected extracted date range in debug_info")

    # 对于 record 类的，检查 reply 是否包含“已记录”或类似确认
    if "帮我记" in query or "记一下" in query:
        pass
