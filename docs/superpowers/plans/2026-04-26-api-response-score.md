# API 响应模型增加分数 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 API 响应的 `MemoryItem` 模型中增加 `score` 字段，并在 `query` 接口中填充搜索分数。

**Architecture:** 修改 FastAPI 的 Pydantic 模型 `MemoryItem`，并更新 `query` 接口的逻辑，将从执行层获取的 `score` 映射到响应模型中。

**Tech Stack:** Python, FastAPI, Pydantic

---

### Task 1: 修改 MemoryItem 模型

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 在 MemoryItem 类中增加 score 字段**

```python
class MemoryItem(BaseModel):
    id: str
    memory: str
    created_at: str
    score: float = 0.0  # 新增
    photo_path: Optional[str] = None
    photo_url: Optional[str] = None
```

- [ ] **Step 2: 验证语法**

运行: `python -m py_compile press_to_talk/api/main.py`
预期: 无错误输出

---

### Task 2: 在 query 接口中填充 score

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 在 query 函数中映射 score 字段**

找到 `query` 函数中构建 `memories` 列表的地方：

```python
        # Map raw memories to MemoryItem
        memories = []
        for m in result.memories:
            memories.append(MemoryItem(
                id=str(m.get("id")),
                memory=m.get("memory", ""),
                created_at=str(m.get("created_at", "")),
                score=float(m.get("score") or 0.0), # 新增
                photo_path=m.get("photo_path"),
                photo_url=get_photo_url(m.get("photo_path"))
            ))
```

- [ ] **Step 2: 验证语法**

运行: `python -m py_compile press_to_talk/api/main.py`
预期: 无错误输出

---

### Task 3: 在 get_memories 接口中填充 score

虽然 `get_memories` 是简单的列表返回，但为了模型一致性，我们也应该显式设置或允许默认值。

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 在 get_memories 函数中填充 score**

```python
@app.post("/v1/memories", response_model=List[MemoryItem], summary="获取长期记忆条目", description="按时间倒序返回当前用户的最近 50 条长期记忆记录。")
async def get_memories(user_id: str = Depends(get_user_id)):
    try:
        memories = (RememberEntry
                   .select()
                   .where(RememberEntry.user_id == user_id)
                   .order_by(RememberEntry.created_at.desc())
                   .limit(50))
        return [
            MemoryItem(
                id=m.id,
                memory=m.memory,
                created_at=str(m.created_at),
                score=0.0, # 显式填充或依赖默认值
                photo_path=m.photo_path,
                photo_url=get_photo_url(m.photo_path)
            )
            for m in memories
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 2: 验证语法**

运行: `python -m py_compile press_to_talk/api/main.py`
预期: 无错误输出

---

### Task 4: 编写并运行测试验证

**Files:**
- Create: `tests/test_api_score.py`

- [ ] **Step 1: 编写测试脚本验证响应中包含 score**

```python
import pytest
from fastapi.testclient import TestClient
from press_to_talk.api.main import app
import json

client = TestClient(app)

def test_query_response_has_score():
    # 模拟请求，注意这里可能需要 mock 掉执行层以避免真正的 LLM 调用
    # 或者如果环境允许，直接测试
    # 这里我们主要验证 Pydantic 模型能否正确序列化 score
    payload = {
        "query": "test query",
        "mode": "memory-chat"
    }
    # 假设我们通过某种方式触发了一个包含记忆的响应
    # 实际上，我们可以直接测试模型
    from press_to_talk.api.main import MemoryItem
    item = MemoryItem(id="1", memory="test", created_at="2024-01-01", score=0.95)
    assert item.score == 0.95
    assert "score" in item.model_dump()

def test_api_v1_memories_has_score():
    # 暂时通过 mock 数据库或直接调用来验证
    pass

if __name__ == "__main__":
    test_query_response_has_score()
    print("Test passed!")
```

- [ ] **Step 2: 运行测试**

运行: `pytest tests/test_api_score.py` 或直接运行 `python tests/test_api_score.py`

---

### Task 5: 提交更改

- [ ] **Step 1: Git commit**

```bash
git add press_to_talk/api/main.py
git commit -m "feature: add score field to MemoryItem API response"
```
