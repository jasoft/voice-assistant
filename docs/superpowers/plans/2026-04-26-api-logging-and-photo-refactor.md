# API 日志增强与 Photo 结构重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增强 Web API 日志输出（包含 Header 和 Body），并将 Photo 字段重构为统一的结构化节点（type + content）。

**Architecture:** 
1. **Middleware**: 在 FastAPI 中使用中间件拦截所有请求，记录详细信息并进行脱敏处理。
2. **Model Refactor**: 升级 Pydantic 模型，将平级字段合并为嵌套对象，利用类型分发增强扩展性。

**Tech Stack:** FastAPI, Pydantic, Python Logging (Rich).

---

### Task 1: 环境准备与自动化测试基准

**Files:**
- Create: `tests/repro_api_changes.py`

- [ ] **Step 1: 编写测试脚本以验证当前 API 行为（及后续新行为）**

```python
import requests
import json

BASE_URL = "http://localhost:10031"
TOKEN = "your_test_token" # 需根据实际环境调整

def test_logging_and_photo():
    # 测试 1: 旧版 Photo 格式 (应在重构后失败)
    old_payload = {
        "query": "你好",
        "photo": "base64_data_here"
    }
    # 测试 2: 新版 Photo 格式 (Base64)
    new_payload_base64 = {
        "query": "保存这张图",
        "photo": {
            "type": "base64",
            "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==",
            "mime": "image/png"
        }
    }
    # 测试 3: 新版 Photo 格式 (URL)
    new_payload_url = {
        "query": "分析这张图",
        "photo": {
            "type": "url",
            "url": "https://www.google.com/images/branding/googlelogo/1x/googlelogo_color_272x92dp.png"
        }
    }
    
    print("Testing API...")
    # 这里仅作为结构参考，实际运行需启动服务器
```

- [ ] **Step 2: 提交基准测试脚本**

```bash
git add tests/repro_api_changes.py
git commit -m "test: add api refactor reproduction script"
```

### Task 2: 实现详细请求日志中间件

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 导入必要的库并实现脱敏函数**

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from ..utils.logging import log, log_multiline

def mask_auth_header(auth_str: str) -> str:
    if not auth_str or len(auth_str) < 10:
        return "***"
    return f"{auth_str[:6]}...{auth_str[-4:]}"
```

- [ ] **Step 2: 编写 LoggingMiddleware 类**

```python
class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/v1"):
            return await call_next(request)

        # 读取 Body
        body = await request.body()
        
        # 记录日志
        headers = dict(request.headers)
        if "authorization" in headers:
            headers["authorization"] = mask_auth_header(headers["authorization"])
        
        body_str = body.decode("utf-8", errors="replace")
        if len(body_str) > 1000:
            body_str = body_str[:1000] + "... [truncated]"

        log_content = [
            f"Method: {request.method}",
            f"URL: {request.url}",
            f"Client: {request.client.host if request.client else 'unknown'}",
            f"Headers: {json.dumps(headers, indent=2)}",
            f"Body: {body_str}"
        ]
        log_multiline("API Request Incoming", "\n".join(log_content), level="info")

        # 将 body 重新封装，以便后续逻辑读取
        async def receive():
            return {"type": "http.request", "body": body}

        request._receive = receive
        response = await call_next(request)
        return response

app.add_middleware(LoggingMiddleware)
```

- [ ] **Step 3: 运行 API 并验证控制台日志输出**
- [ ] **Step 4: 提交代码**

```bash
git add press_to_talk/api/main.py
git commit -m "feat: add detailed request logging middleware to API"
```

### Task 3: 重构 Photo 数据模型

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 定义 PhotoAttachment 模型**

```python
class PhotoAttachment(BaseModel):
    type: str = Field(..., description="图片类型: 'url' 或 'base64'")
    url: Optional[str] = Field(None, description="当 type 为 'url' 时必填")
    data: Optional[str] = Field(None, description="当 type 为 'base64' 时必填 (Base64 数据)")
    mime: Optional[str] = Field(None, description="可选，图片的 MIME 类型")
```

- [ ] **Step 2: 更新 QueryRequest 模型**

```python
# 找到 QueryRequest 类，修改 photo 字段
class QueryRequest(BaseModel):
    query: str = ...
    mode: Optional[ExecutionMode] = ...
    photo: Optional[PhotoAttachment] = Field(None, description="图片附件节点")

    class Config:
        json_schema_extra = {
            "example": {
                "query": "最近三天的记录",
                "mode": "memory-chat",
                "photo": {
                    "type": "base64",
                    "data": "...",
                    "mime": "image/jpeg"
                }
            }
        }
```

- [ ] **Step 3: 提交模型变更**

```bash
git add press_to_talk/api/main.py
git commit -m "refactor: restructure photo field in QueryRequest"
```

### Task 4: 更新业务逻辑处理

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 更新 query 接口中的图片处理逻辑**

```python
        # Handle photo attachment
        photo_path = None
        if req.photo:
            try:
                photo_dir = os.path.join("data", "photos")
                os.makedirs(photo_dir, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unique_id = uuid.uuid4().hex[:8]
                
                if req.photo.type == "base64":
                    b64_str = req.photo.data
                    if "," in b64_str:
                        b64_str = b64_str.split(",")[1]
                    photo_bytes = base64.b64decode(b64_str)
                    filename = f"photo_{timestamp}_{unique_id}.jpg"
                    full_path = os.path.join(photo_dir, filename)
                    with open(full_path, "wb") as f:
                        f.write(photo_bytes)
                    photo_path = f"photos/{filename}"
                elif req.photo.type == "url":
                    # 简单实现：下载 URL 
                    import httpx
                    filename = f"photo_{timestamp}_{unique_id}.jpg"
                    full_path = os.path.join(photo_dir, filename)
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(req.photo.url)
                        if resp.status_code == 200:
                            with open(full_path, "wb") as f:
                                f.write(resp.content)
                            photo_path = f"photos/{filename}"
            except Exception as photo_err:
                log(f"Warning: Failed to process photo: {photo_err}", level="warn")
```

- [ ] **Step 2: 验证各种图片输入情况**
- [ ] **Step 3: 提交代码**

```bash
git add press_to_talk/api/main.py
git commit -m "feat: implement structured photo processing in query API"
```

### Task 5: 文档与清理

**Files:**
- Modify: `docs/system_architecture.md` (如有必要)
- Remove: `tests/repro_api_changes.py`

- [ ] **Step 1: 更新系统架构文档中的 API 说明**
- [ ] **Step 2: 删除临时测试脚本**
- [ ] **Step 3: 最终提交**

```bash
git add .
git commit -m "docs: update api documentation for photo refactor"
```
