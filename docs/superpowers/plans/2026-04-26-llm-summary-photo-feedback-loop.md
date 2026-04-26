# 检索总结逻辑增强与 Photo URL 闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 解决大模型总结后丢失图片信息的问题。通过在 Prompt 中引入 ID，让模型选择记录，并在行为树中反查并返回对应的 `photo_url`。

**Architecture:** 
1. **Prompt Injection**: 在总结阶段将记录 ID 喂给 LLM。
2. **LLM Selection**: 要求 LLM 在回复末尾标注选中的 ID 列表。
3. **Blackboard Enrichment**: 在 BT 节点解析回复，提取 ID 并解析为可用的 Web URL。
4. **API Propagation**: 将解析出的 URL 透传给最终响应。

**Tech Stack:** Python, FastAPI, Pydantic, BehaviorTree.

---

### Task 1: 增强 LLM 总结输入的上下文

**Files:**
- Modify: `press_to_talk/agent/agent.py`

- [ ] **Step 1: 修改 _summarize_remember_output 函数，在提示词中包含 ID**

```python
# 找到循环处理 items 的部分
for item in items:
    mem_id = item.get("id", "unknown") # 确保获取 ID
    mem_text = str(item.get("memory", "")).strip()
    if mem_text:
        date_prefix = _memory_date_prefix(
            str(item.get("updated_at") or item.get("created_at") or "")
        )
        # 核心改动：在文本前加上 [ID: xxx]
        prefix = f"[ID: {mem_id}]"
        if date_prefix:
            extracted_memories.append(f"{prefix} {date_prefix}: {mem_text}")
        else:
            extracted_memories.append(f"{prefix} {mem_text}")
```

- [ ] **Step 2: 提交代码变更**

```bash
git add press_to_talk/agent/agent.py
git commit -m "feat: include record IDs in memory summary input for LLM"
```

### Task 2: 更新 Prompt 引导模型输出 ID

**Files:**
- Modify: `workflow_config.json`

- [ ] **Step 1: 更新 remember_summary 的 system_prompt**

```json
"remember_summary": {
    "system_prompt": "... (原有规则) ... \n\n7. **特别要求**：如果你的回复参考了某条记忆，请在回复的最后一行，以 `[SELECTED_IDS: id1, id2]` 的格式列出你引用的所有 ID。如果没有引用任何记录，请输出 `[SELECTED_IDS: none]`。绝对不要漏掉这个标记。"
}
```

- [ ] **Step 2: 提交配置变更**

```bash
git add workflow_config.json
git commit -m "docs: update summary prompt to require SELECTED_IDS tag"
```

### Task 3: 在行为树节点中解析 ID 并解析 URL

**Files:**
- Modify: `press_to_talk/execution/bt/nodes.py`

- [ ] **Step 1: 在 LLMSummarizeAction 中实现 ID 解析逻辑**

```python
# 引入之前写的 get_photo_url
from ...api.main import get_photo_url

# 在 LLMSummarizeAction.tick 内部
# 1. 获取 LLM 回复
full_reply = bb.reply
# 2. 提取 [SELECTED_IDS: ...]
match = re.search(r"\[SELECTED_IDS:\s*([^\]]+)\]", full_reply)
selected_ids = []
if match:
    ids_str = match.group(1).strip()
    if ids_str.lower() != "none":
        selected_ids = [i.strip() for i in ids_str.split(",")]
    # 清理回复中的标记，以免显示给用户
    bb.reply = re.sub(r"\[SELECTED_IDS:\s*[^\]]+\]", "", full_reply).strip()

# 3. 反查 photo_url
resolved_urls = []
if selected_ids and bb.memories_raw:
    # bb.memories_raw 是原始 JSON 字符串
    raw_data = json.loads(bb.memories_raw)
    items = raw_data.get("results", []) or raw_data.get("items", [])
    
    # 创建 ID 到 photo_path 的映射
    path_map = {str(item.get("id")): item.get("photo_path") for item in items}
    
    for rid in selected_ids:
        path = path_map.get(rid)
        if path:
            url = get_photo_url(path)
            if url:
                resolved_urls.append(url)

# 4. 存入黑板
bb.reply_photos = resolved_urls # 保存列表
```

- [ ] **Step 2: 提交代码变更**

```bash
git add press_to_talk/execution/bt/nodes.py
git commit -m "feat: parse selected IDs and resolve photo URLs in BT nodes"
```

### Task 4: 更新 API 以支持返回图片列表

**Files:**
- Modify: `press_to_talk/api/main.py`
- Modify: `press_to_talk/execution/__init__.py` (或执行入口)

- [ ] **Step 1: 更新 QueryResponse 支持多图 (兼容旧版)**

```python
class QueryResponse(BaseModel):
    reply: str
    photo_url: Optional[str] = ... # 保留第一个，兼容旧版
    photo_urls: List[str] = Field(default_factory=list, description="图片访问 URL 列表")
```

- [ ] **Step 2: 在执行入口中捕获黑板中的图片列表并返回**

```python
# 修改 execute_transcript_async 或相关调用处
# 确保能够获取到行为树产生的 bb.reply_photos
```

- [ ] **Step 3: 提交代码变更**

```bash
git add .
git commit -m "feat: support multiple photo URLs in API response"
```

### Task 5: 验证闭环

- [ ] **Step 1: 提问一个包含图片的记忆（如“我昨天存的照片是什么”）**
- [ ] **Step 2: 检查 API 返回的 JSON，确认 photo_urls 包含正确的 /assets/ 链接**
- [ ] **Step 3: 检查日志，确认 LLM 正确输出了 [SELECTED_IDS: ...] 标记**

---

### Task 6: 最终清理

- [ ] **Step 1: 更新架构文档说明闭环逻辑**
- [ ] **Step 2: 最终提交**
