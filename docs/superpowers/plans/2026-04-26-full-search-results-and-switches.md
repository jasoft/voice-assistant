# 检索全量透传与双源搜索控制实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 取消对 LLM 选择记录的过度依赖，改为全量返回搜索结果并携带分数；增加关键字/语义搜索的独立开关。

**Architecture:** 
1. **Config**: 引入 `PTT_ENABLE_KEYWORD_SEARCH` 和 `PTT_ENABLE_SEMANTIC_SEARCH`。
2. **Storage**: 在 `SQLiteFTS5RememberStore` 中应用开关。
3. **Execution**: 简化 `execute_transcript_async`，不再根据 LLM 回复过滤 ID，改为全量透传搜索结果。
4. **API**: `MemoryItem` 增加 `score` 字段。

---

### Task 1: 配置项与数据模型升级

**Files:**
- Modify: `press_to_talk/storage/models.py`
- Modify: `press_to_talk/models/config.py`
- Modify: `press_to_talk/storage/service.py`

- [ ] **Step 1: 在 StorageConfig 中添加控制开关**
- [ ] **Step 2: 在全局 Config 中添加控制开关并从环境变量读取**
- [ ] **Step 3: 在 load_storage_config 中映射这些开关**

### Task 2: 存储层检索开关与分数规范化

**Files:**
- Modify: `press_to_talk/storage/providers/sqlite_fts.py`

- [ ] **Step 1: 在 find 方法开头判断开关**
- [ ] **Step 2: 确保所有返回项都包含 score 且格式为 float**
- [ ] **Step 3: 确保 extract_sqlite_summary_payload 保留 score 字段**

### Task 3: 简化执行层，实现全量透传

**Files:**
- Modify: `press_to_talk/execution/__init__.py`

- [ ] **Step 1: 移除“统一闭环解析逻辑”及其相关正则表达式匹配**
- [ ] **Step 2: 直接从 Blackboard 提取全量 memories**

```python
# 修改后逻辑
await tree.tick(bb)
# 只要搜到了，就全给
all_memories = bb.memories # 注意：这是经过解析后的 list[dict]
return ExecutionResult(reply=bb.reply, memories=all_memories)
```

### Task 4: API 响应模型增加分数

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 在 MemoryItem 模型中增加 score: float = 0.0 字段**
- [ ] **Step 2: 在 query 接口中填充 score**

### Task 5: 环境验证与搜索模式测试

- [ ] **Step 1: 修改 .env 设置 PTT_ENABLE_SEMANTIC_SEARCH=false，验证关键字搜索效果**
- [ ] **Step 2: 设置 PTT_ENABLE_KEYWORD_SEARCH=false，验证语义搜索效果**
- [ ] **Step 3: 观察 JSON 回复，确认 memories 列表包含所有命中项及 score**

---

### Task 6: 清理过时的 Prompt 要求 (可选)

**Files:**
- Modify: `workflow_config.json`

- [ ] **Step 1: 弱化对 SELECTED_IDS 的强制要求 (虽然不再解析，但让模型保持整洁也是好的)**
