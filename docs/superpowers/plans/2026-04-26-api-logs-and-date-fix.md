# API 详细日志持久化与日期查询容错实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Web API 启用持久化文件日志，并修复存储层在处理日期范围查询时因大模型算反起始/结束日期而导致 0 结果的 Bug。

**Architecture:** 
1. **Logging**: 在 `press_to_talk/api/main.py` 的启动生命周期中，调用 `init_session_log` 初始化文件日志，记录到 `logs/api_requests.log`。
2. **Logic Fix**: 在 `press_to_talk/storage/providers/sqlite_fts.py` 中，在执行日期范围过滤前，检查 `start_date` 是否晚于 `end_date`，若是则交换。

**Tech Stack:** FastAPI Lifespan, Python Logging, SQLite.

---

### Task 1: 为 Web API 启用文件日志持久化

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 在 API 生命周期管理中初始化日志**

```python
# 在 lifespan 中添加初始化和关闭逻辑
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化
    from ..utils.logging import init_session_log, log, close_session_log
    from pathlib import Path
    log_path = init_session_log(Path("logs"), session_id="api-server")
    log(f"API Server started. Detailed logs at: {log_path}", level="info")
    
    yield
    
    # 关闭时释放
    log("API Server shutting down.", level="info")
    close_session_log()
```

- [ ] **Step 2: 提交代码变更**

```bash
git add press_to_talk/api/main.py
git commit -m "feat(api): enable persistent file logging for API server"
```

### Task 2: 存储层日期范围逻辑容错

**Files:**
- Modify: `press_to_talk/storage/providers/sqlite_fts.py`

- [ ] **Step 1: 修改 find 方法，增加日期交换逻辑**

```python
# 找到 find 方法中处理日期范围查询的部分 (约 730 行)
def find(self, *, query: str, min_score: float = 0.0, start_date: str | None = None, end_date: str | None = None) -> str:
    # 新增容错逻辑：
    if start_date and end_date:
        if start_date > end_date:
            log(f"Detected inverted dates: start={start_date}, end={end_date}. Swapping.", level="warn")
            start_date, end_date = end_date, start_date

    # 1. 优先处理日期范围查询
    if start_date or end_date:
        # ... 现有逻辑 ...
```

- [ ] **Step 2: 编写测试用例验证容错**

```python
def test_inverted_date_search():
    # 模拟 start="2026-04-26", end="2026-04-20"
    # 验证是否能查到 20-26 号之间的记录
```

- [ ] **Step 3: 提交代码变更**

```bash
git add press_to_talk/storage/providers/sqlite_fts.py
git commit -m "fix(storage): auto-swap inverted dates in range queries"
```

### Task 4: 验证与复现

- [ ] **Step 1: 手动触发一个“算反了”的请求**

```bash
curl -X POST http://localhost:10031/v1/query \
-H "Content-Type: application/json" \
-H "Authorization: Bearer soj-default-token" \
-d '{"query": "最近三天的记录"}'
```

- [ ] **Step 2: 检查 logs/ 目录下的最新日志文件，确保记录了详细的搜索参数。**
- [ ] **Step 3: 确认即使模型给的是反的，JSON 响应中 memories 列表也能正常带回数据。**
