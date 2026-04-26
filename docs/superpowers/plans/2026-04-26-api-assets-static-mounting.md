# API 静态资源访问与路径转换实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 FastAPI 中挂载图片目录到 `/assets`，并让 API 返回可直接访问的 `photo_url`。

**Architecture:** 
1. **Static Mounting**: 使用 `fastapi.staticfiles.StaticFiles` 挂载 `data/photos`。
2. **URL Mapping**: 实现路径转换工具，将数据库路径映射为 Web 访问路径。
3. **Model Update**: 升级响应模型，增加 `photo_url` 字段。

**Tech Stack:** FastAPI, Pydantic.

---

### Task 1: 基础设施与辅助函数实现

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 导入 StaticFiles 并实现转换函数**

```python
from fastapi.staticfiles import StaticFiles

def get_photo_url(photo_path: Optional[str]) -> Optional[str]:
    """Convert database photo path to web accessible URL."""
    if not photo_path:
        return None
    # photo_path is typically "photos/filename.jpg"
    # we want to map it to "/assets/filename.jpg"
    filename = os.path.basename(photo_path)
    return f"/assets/{filename}"
```

- [ ] **Step 2: 挂载静态目录**

```python
# 在 app = FastAPI(...) 之后添加
app.mount("/assets", StaticFiles(directory="data/photos"), name="assets")
```

- [ ] **Step 3: 提交基础改动**

```bash
git add press_to_talk/api/main.py
git commit -m "feat: mount static photos directory to /assets"
```

### Task 2: 更新数据模型

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 更新 QueryResponse 增加 photo_url**

```python
class QueryResponse(BaseModel):
    reply: str
    photo_url: Optional[str] = Field(None, description="图片访问 URL")
```

- [ ] **Step 2: 更新 MemoryItem 增加 photo_url**

```python
class MemoryItem(BaseModel):
    id: str
    memory: str
    created_at: str
    photo_path: Optional[str] = None
    photo_url: Optional[str] = None # 新增
```

- [ ] **Step 3: 提交模型变更**

```bash
git add press_to_talk/api/main.py
git commit -m "refactor: add photo_url field to API response models"
```

### Task 3: 更新接口逻辑

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 更新 query 接口返回值**

```python
# 找到 query 函数末尾
return QueryResponse(reply=reply, photo_url=get_photo_url(photo_path))
```

- [ ] **Step 2: 更新 get_history 接口返回值**
*(注：如果 HistoryItem 也需要，同步修改，但计划中优先保证 Query 和 Memories)*

- [ ] **Step 3: 更新 get_memories 接口返回值**

```python
# 找到 get_memories 函数中生成列表的部分
return [
    MemoryItem(
        id=m.id,
        memory=m.memory,
        created_at=str(m.created_at),
        photo_path=m.photo_path,
        photo_url=get_photo_url(m.photo_path) # 新增
    )
    for m in memories
]
```

- [ ] **Step 4: 提交接口逻辑变更**

```bash
git add press_to_talk/api/main.py
git commit -m "feat: populate photo_url in query and memories APIs"
```

### Task 4: 验证外部访问

- [ ] **Step 1: 启动服务并上传一张测试图片**
- [ ] **Step 2: 使用 curl 或浏览器检查返回的 photo_url 是否正确**
- [ ] **Step 3: 尝试通过外部地址访问该图片**

运行验证命令：
```bash
# 假设返回的 url 是 /assets/test.jpg
curl -I http://va-dev.soj.myds.me:1443/assets/test.jpg
```
预期结果：`HTTP/1.1 200 OK` 且 Content-Type 是 `image/jpeg`。

---

### Task 5: 最终清理与文档

- [ ] **Step 1: 更新 docs/system_architecture.md 说明新的图片访问方式**
- [ ] **Step 2: 最终提交**

```bash
git add .
git commit -m "docs: document static asset access and url mapping"
```
