# 强化记录意图触发与图片保存闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 确保只要请求中包含图片，系统就强制进入记录流程（Record Mode），并修复 URL 类型图片的保存问题。

**Architecture:** 
1. **BT Condition Override**: 在 `IsRecordIntent` 节点中增加对 `photo_path` 的检查。
2. **Intent Forced Correction**: 如果存在图片，强制将意图修正为 `record`。
3. **API Robustness**: 改进 `api/main.py` 对 URL 附件的处理。

---

### Task 1: 行为树意图触发逻辑强化

**Files:**
- Modify: `press_to_talk/execution/bt/nodes.py`

- [ ] **Step 1: 修改 IsRecordIntent 支持图片强制触发**

```python
class IsRecordIntent(Condition):
    async def tick(self, bb: Blackboard) -> Status:
        # 如果带有图片，强制判定为记录意图
        if bb.photo_path:
            return Status.SUCCESS
        if bb.intent.get("intent") == "record":
            return Status.SUCCESS
        return Status.FAILURE
```

- [ ] **Step 2: 修改 ExtractIntentAction 或 SetDefaultIntentAction 确保意图一致性**

```python
# 在 ExtractIntentAction 末尾增加逻辑
if bb.photo_path and bb.intent.get("intent") != "record":
    # 强制修正意图为 record，防止 ExecuteRecordAction 拿不到正确的 arguments
    bb.intent["intent"] = "record"
    # 如果没有 args，至少给个基础的
    if "args" not in bb.intent:
        bb.intent["args"] = {"memory": bb.transcript}
```

### Task 2: 修复 API URL 图片保存失败问题

**Files:**
- Modify: `press_to_talk/api/main.py`

- [ ] **Step 1: 改进 URL 处理逻辑，增加详细日志并确保 photo_path 成功赋值**

```python
                elif req.photo.type == "url":
                    # ... 现有的下载逻辑 ...
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        try:
                            resp = await client.get(req.photo.url)
                            if resp.status_code == 200:
                                with open(full_path, "wb") as f:
                                    f.write(resp.content)
                                photo_path = f"photos/{filename}"
                                log(f"Successfully downloaded photo from URL to {photo_path}")
                            else:
                                log(f"Failed to download photo. Status: {resp.status_code}", level="error")
                        except Exception as e:
                            log(f"Error downloading photo: {e}", level="error")
```

### Task 3: 验证

- [ ] **Step 1: 发送带图片的 query（不论文字内容是什么）**
- [ ] **Step 2: 检查数据库，确认记录已存入且 photo_path 字段非 null**
- [ ] **Step 3: 观察回复，确认走的是记录路径**
