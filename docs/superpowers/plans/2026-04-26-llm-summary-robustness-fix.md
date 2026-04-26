# LLM 总结闭环鲁棒性增强实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 解决大模型可能不输出 `[SELECTED_IDS]` 标记的问题。通过优化提示词优先级和增加后端基于文本包含关系的自动 ID 匹配，确保图片和原始 JSON 能够可靠返回。

**Architecture:** 
1. **Prompt Priority**: 在 `workflow_config.json` 中明确区分正文（忽略 ID）和结尾（强制输出 ID）的规则。
2. **Fuzzy Matching Fallback**: 在 `nodes.py` 中，如果正则解析不到 `[SELECTED_IDS]`，则尝试将回复中的每一行与原始记忆进行模糊匹配，自动推断 ID。

**Tech Stack:** Python, Regex, BehaviorTree.

---

### Task 1: 优化大模型提示词优先级

**Files:**
- Modify: `workflow_config.json`

- [ ] **Step 1: 重写 remember_summary 提示词**

```json
"remember_summary": {
    "system_prompt": "你是一个智能助手。今天是 ${PTT_CURRENT_TIME}。\n\n【核心规则】\n1. 基于提供的数据库结果友好回答问题。如果结果无关，请直接回答原问题。\n2. **格式要求**：多条记录必须分行列出，严禁合并段落。\n3. **日期表达**：2026年记录直接说“M月D日”，去年说“去年M月D日”。\n4. **人称指代**：直接对我说话，用“你”称呼我。禁止把自己说成当事人。\n\n【ID 引用规则（极重要）】\n5. **正文隐藏**：在回答的文字正文中，**严禁**出现 [ID: xxx] 这种标记，保持回复纯净。\n6. **结尾强制标注**：在回复的**最后一行**，必须以 `[SELECTED_IDS: id1, id2]` 的格式列出你引用的所有记忆 ID。如果没有引用，输出 `[SELECTED_IDS: none]`。这是一个机器解析标记，绝对不能省略。"
}
```

- [ ] **Step 2: 提交配置变更**

```bash
git add workflow_config.json
git commit -m "docs: optimize summary prompt for better ID output reliability"
```

### Task 2: 实现基于文本包含的自动 ID 匹配兜底

**Files:**
- Modify: `press_to_talk/execution/bt/nodes.py`

- [ ] **Step 1: 在 LLMSummarizeAction.tick 中增加兜底逻辑**

```python
# ... 在正则匹配失败后的部分 ...
if not selected_ids and bb.memories_raw:
    # 兜底：如果模型没给 ID，我们根据回复文本反推
    try:
        raw_data = json.loads(bb.memories_raw)
        all_items = raw_data.get("results", []) or raw_data.get("items", [])
        
        # 遍历每条原始记忆，如果回复里提到了记忆的核心内容，就认为被选中了
        for item in all_items:
            mem_text = item.get("memory", "")
            # 简单的包含判定（可以根据需要加强，比如匹配关键实体）
            if mem_text and mem_text in bb.reply:
                selected_ids.append(str(item.get("id")))
    except Exception as e:
        log(f"Fuzzy match fallback failed: {e}", level="warn")
```

- [ ] **Step 2: 增强正则表达式，使其更兼容（如处理多余空格或括号）**

```python
match = re.search(r"\[SELECTED_IDS[:：]\s*([^\]]+)\]", full_reply, re.IGNORECASE)
```

- [ ] **Step 3: 提交代码变更**

```bash
git add press_to_talk/execution/bt/nodes.py
git commit -m "feat: add fuzzy text matching fallback for record selection"
```

### Task 3: 验证并回归

- [ ] **Step 1: 使用大王刚才失败的“最近三天的记录”作为测试输入**
- [ ] **Step 2: 观察日志，验证即使模型不输出标记，后端是否也能捞出 ID**
- [ ] **Step 3: 最终确认 JSON 返回中 memories 字段是否已填充**
