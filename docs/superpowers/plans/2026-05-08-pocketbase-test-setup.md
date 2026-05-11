# PocketBase Setup and Test Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure PocketBase in `.env` and implement automated setup/teardown in tests to ensure reliable E2E testing.

**Architecture:** 
- Add `PTT_PB_URL` to `.env`.
- Create a pytest fixture in `tests/conftest.py` that starts PocketBase before tests and stops it after.
- Use a dedicated temporary data directory for tests to avoid polluting production/development data.

**Tech Stack:** Python, pytest, PocketBase, shell scripting.

---

### Task 1: Update .env configuration

**Files:**
- Modify: `.env`

- [ ] **Step 1: Add PocketBase URL to .env**

Add the following line if it doesn't exist:
```env
PTT_PB_URL=http://127.0.0.1:18090
```

- [ ] **Step 2: Verify .env update**
Run: `cat .env | grep PTT_PB_URL`
Expected: `PTT_PB_URL=http://127.0.0.1:18090`

- [ ] **Step 3: Commit**
```bash
git add .env
git commit -m "config: add PocketBase URL to .env"
```

### Task 2: Implement PocketBase Test Fixture

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add pocketbase fixture to conftest.py**

```python
import os
import subprocess
import time
import signal
import pytest
import requests

@pytest.fixture(scope="session", autouse=True)
def pocketbase_server():
    """
    启动 PocketBase 并在测试结束后关闭。
    使用不同的数据目录以避免干扰开发环境。
    """
    pb_url = os.environ.get("PTT_PB_URL", "http://127.0.0.1:18090")
    # 提取端口
    port = pb_url.split(":")[-1]
    
    pb_bin = "./.pocketbase/pocketbase"
    pb_data_dir = "./.pocketbase/pb_data_test"
    
    # 确保二进制文件存在
    if not os.path.exists(pb_bin):
        # 尝试运行下载脚本
        subprocess.run(["./scripts/start_pocketbase.sh", "--help"], capture_output=True)
    
    # 启动进程
    process = subprocess.Popen(
        [pb_bin, "serve", "--http", f"127.0.0.1:{port}", "--dir", pb_data_dir],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid
    )
    
    # 等待启动
    max_retries = 10
    for i in range(max_retries):
        try:
            requests.get(f"{pb_url}/api/health", timeout=1)
            break
        except requests.exceptions.RequestException:
            if i == max_retries - 1:
                process.kill()
                raise RuntimeError("PocketBase failed to start for tests")
            time.sleep(1)
    
    yield
    
    # 停止进程
    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
```

- [ ] **Step 2: Verify fixture works by running tests**
Run: `uv run pytest -m e2e tests/test_e2e_pocketbase.py`
Expected: PASS

- [ ] **Step 3: Commit**
```bash
git add tests/conftest.py
git commit -m "test: add automated PocketBase setup/teardown fixture"
```

### Task 3: Cleanup Test Data

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add cleanup logic to teardown**

```python
    # ... after yield ...
    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    # 清理测试数据目录
    import shutil
    if os.path.exists(pb_data_dir):
        shutil.rmtree(pb_data_dir)
```

- [ ] **Step 2: Verify cleanup**
Run tests and check if `./.pocketbase/pb_data_test` exists after tests.

- [ ] **Step 3: Commit**
```bash
git add tests/conftest.py
git commit -m "test: cleanup PocketBase test data after sessions"
```
