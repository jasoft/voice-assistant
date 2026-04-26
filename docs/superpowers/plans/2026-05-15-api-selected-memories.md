# API Selected Memories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the API to return the full JSON of memories selected by the LLM during summarization.

**Architecture:** Data flows from Behavior Tree nodes to the Blackboard, then to the ExecutionResult, and finally to the API response model.

**Tech Stack:** Python, FastAPI, Pydantic, Behavior Tree.

---

### Task 1: Update Blackboard

**Files:**
- Modify: `press_to_talk/execution/bt/base.py`

- [ ] **Step 1: Add selected_memories to Blackboard**

```python
@dataclass
class Blackboard:
    # ... existing fields
    selected_memories: List[dict] = field(default_factory=list)
```

- [ ] **Step 2: Commit changes**

```bash
git add press_to_talk/execution/bt/base.py
git commit -m "feat: add selected_memories to Blackboard"
```

---

### Task 2: Update ExecutionResult

**Files:**
- Modify: `press_to_talk/execution/__init__.py`

- [ ] **Step 1: Add memories to ExecutionResult**

```python
@dataclass
class ExecutionResult:
    reply: str
    photos: List[str] = field(default_factory=list)
    memories: List[dict] = field(default_factory=list)
```

- [ ] **Step 2: Update execute_transcript_async to populate memories**

```python
async def execute_transcript_async(cfg: Any, transcript: str, photo_path: str | None = None) -> ExecutionResult:
    # ...
    if bb.reply:
        return ExecutionResult(reply=bb.reply, photos=bb.reply_photos, memories=bb.selected_memories)
```

- [ ] **Step 3: Commit changes**

```bash
git add press_to_talk/execution/__init__.py
git commit -m "feat: add memories to ExecutionResult"
```

---

### Task 3: Capture full items in LLMSummarizeAction

**Files:**
- Modify: `press_to_talk/execution/bt/nodes.py`

- [ ] **Step 1: Update LLMSummarizeAction to store full items**

```python
            # 2. 反查 photo_url 和完整 item
            resolved_urls = []
            selected_items = []
            if selected_ids and bb.memories_raw:
                try:
                    raw_data = json.loads(bb.memories_raw)
                    items = raw_data.get("results", []) or raw_data.get("items", [])
                    
                    # 创建 ID 到 item 的映射
                    item_map = {str(item.get("id")): item for item in items if item.get("id")}
                    
                    for rid in selected_ids:
                        item = item_map.get(rid)
                        if item:
                            selected_items.append(item)
                            path = item.get("photo_path")
                            if path:
                                url = get_photo_url(path)
                                if url:
                                    resolved_urls.append(url)
                except Exception:
                    pass
            
            # 3. 存入黑板
            bb.reply_photos = resolved_urls
            bb.selected_memories = selected_items
```

- [ ] **Step 2: Commit changes**

```bash
git add press_to_talk/execution/bt/nodes.py
git commit -m "feat: capture full selected items in LLMSummarizeAction"
```

---

### Task 4: Update API Response

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: Update QueryResponse model**

```python
class QueryResponse(BaseModel):
    reply: str
    photo_url: Optional[str] = Field(None, description="首张图片访问 URL (兼容旧版)")
    photo_urls: List[str] = Field(default_factory=list, description="图片访问 URL 列表")
    memories: List[MemoryItem] = Field(default_factory=list, description="所选记忆的完整数据")
```

- [ ] **Step 2: Populate memories in query endpoint**

```python
        # Convert photo paths to URLs
        all_photo_urls = [get_photo_url(p) for p in result.photos if p]
        
        # Map raw memories to MemoryItem
        memories = []
        for m in result.memories:
            memories.append(MemoryItem(
                id=str(m.get("id")),
                memory=m.get("memory", ""),
                created_at=str(m.get("created_at", "")),
                photo_path=m.get("photo_path"),
                photo_url=get_photo_url(m.get("photo_path"))
            ))

        return QueryResponse(
            reply=result.reply, 
            photo_url=first_photo_url,
            photo_urls=all_photo_urls,
            memories=memories
        )
```

- [ ] **Step 3: Commit changes**

```bash
git add press_to_talk/api/main.py
git commit -m "feat: return selected memories in API response"
```

---

### Verification

- [ ] **Step 1: Run smoke tests**
Run: `pytest tests/test_smoke_check.py`

- [ ] **Step 2: Manual verification of API (optional)**
If possible, run a mock query that triggers memory summarization and check the JSON output.
