# Memory Distillation Optimization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optimize the memory recording logic to ensure transcripts are distilled into concise, facts-only memories instead of near-exact transcriptions.

**Architecture:** 
1. Move distillation prompt to `workflow_config.json`.
2. Enhance `intent_extractor` prompt to encourage distillation.
3. Update `agent.py` to use config-based distillation.
4. (Optional) Enhance behavior tree to ensure distillation for all records.

**Tech Stack:** Python, JSON

---

### Task 1: Update Workflow Configuration

**Files:**
- Modify: `workflow_config.json`

- [ ] **Step 1: Add `distill_memory` prompt and enhance `intent_extractor`**

Update `prompts` section to include a dedicated `distill_memory` prompt, and relax the constraints in `intent_extractor` to encourage summary over verbatim copying. Update examples to show distilled results.

```json
// ... in prompts ...
"distill_memory": {
    "system_prompt": "你是一个记忆提炼专家。请忽略用户语音听写中的口语废话、重复、纠错痕迹（如“那个”、“呃”、“帮我记下”），仅提取其中的核心事实信息。提炼成一条自然、准确、简短的中文陈述句，适合作为长期记忆保存。禁止解释，不要前缀，直接输出提炼后的句子。"
},
// ... update intent_extractor instructions and examples ...
```

- [ ] **Step 2: Commit changes**

```bash
git add workflow_config.json
git commit -m "chore: move distillation prompt to config and optimize record instructions"
```

### Task 2: Refactor Agent Distillation Logic

**Files:**
- Modify: `press_to_talk/agent/agent.py`

- [ ] **Step 1: Update `_distill_memory` to use config**

```python
    async def _distill_memory(self, user_input: str) -> str:
        prompts = self.workflow.get("prompts", {})
        distill_cfg = prompts.get("distill_memory", {})
        system_prompt = distill_cfg.get("system_prompt", "你是一个记忆提炼专家。")
        # ... rest of the logic ...
```

- [ ] **Step 2: Update `_get_remember_tools` description**

Improve the description of `remember_add` to further encourage distillation.

- [ ] **Step 3: Commit changes**

```bash
git add press_to_talk/agent/agent.py
git commit -m "refactor: use config-based distillation and improve tool descriptions"
```

### Task 3: Enhance Behavior Tree for Universal Distillation (Verification)

**Files:**
- Modify: `press_to_talk/execution/bt/nodes.py`

- [ ] **Step 1: Ensure `ExecuteRecordAction` performs final distillation if needed**

Check if `bb.intent["args"]["memory"]` should be distilled one last time if it seems too "messy". Actually, the `intent_extractor` should handle it if the prompt is strong enough. I will double check the current `ExtractIntentAction`.

- [ ] **Step 2: Verification**

Run a reproduction script to verify that a messy input like "帮我记一下那个护照在书房柜子里呃就是那个白色的柜子第一个抽屉" becomes "护照在书房白色柜子第一个抽屉里".

- [ ] **Step 3: Commit changes**

```bash
git add press_to_talk/execution/bt/nodes.py
git commit -m "feat: ensure high-quality distillation in behavior tree"
```

---

### Task 4: Final Validation

- [ ] **Step 1: Run CI checks**
Run: `python3 scripts/ci_check.py`

- [ ] **Step 2: Final Commit**
```bash
git commit -m "fix: resolve memory storage being too verbatim by improving distillation logic"
```
