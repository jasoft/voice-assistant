# Final BT Cleanup and Test Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up redundant Behavior Tree blackboard fields and align tests with the "full passthrough" strategy.

**Architecture:** Remove `selected_memories` from `Blackboard` and `nodes.py`. Update tests to verify that `bb.memories` (or the returned result) contains the expected data directly.

**Tech Stack:** Python, Pytest, Behavior Tree (py_trees inspired)

---

### Task 1: Cleanup Blackboard and Nodes

**Files:**
- Modify: `press_to_talk/execution/bt/base.py`
- Modify: `press_to_talk/execution/bt/nodes.py`

- [ ] **Step 1: Remove `selected_memories` from `Blackboard`**

```python
# In press_to_talk/execution/bt/base.py
@dataclass
class Blackboard:
    # ...
    # Remove this line:
    # selected_memories: List[dict] = field(default_factory=list)
```

- [ ] **Step 2: Clean up redundant assignments in `nodes.py`**

Remove assignments to `bb.reply_photos` and `bb.selected_memories`.

- [ ] **Step 3: Commit cleanup**

```bash
git add press_to_talk/execution/bt/base.py press_to_talk/execution/bt/nodes.py
git commit -m "chore: cleanup redundant blackboard fields and assignments"
```

### Task 2: Fix Tests in `test_bt_nodes.py`

**Files:**
- Modify: `tests/test_bt_nodes.py`

- [ ] **Step 1: Update `test_bt_nodes.py` to remove `reply_photos` assertions**

The photos are now handled via `bb.memories` records (which contain photo paths) rather than a separate list.

- [ ] **Step 2: Run tests to verify**

Run: `pytest tests/test_bt_nodes.py`

- [ ] **Step 3: Commit test fixes**

```bash
git add tests/test_bt_nodes.py
git commit -m "fix: update test_bt_nodes to match new behavior"
```

### Task 3: Fix Tests in `test_fuzzy_match_fallback.py`

**Files:**
- Modify: `tests/test_fuzzy_match_fallback.py`

- [ ] **Step 1: Update `test_fuzzy_match_fallback.py`**

Replace `bb.selected_memories` with `bb.memories` or check the final output. Remove `bb.reply_photos` checks.

- [ ] **Step 2: Run tests to verify**

Run: `pytest tests/test_fuzzy_match_fallback.py`

- [ ] **Step 3: Commit test fixes**

```bash
git add tests/test_fuzzy_match_fallback.py
git commit -m "fix: update test_fuzzy_match_fallback to match full passthrough strategy"
```

### Task 4: Update Documentation

**Files:**
- Modify: `docs/system_architecture.md`

- [ ] **Step 1: Update architecture docs**

Remove mentions of `bb.reply_photos` as a separate field.

- [ ] **Step 2: Commit docs**

```bash
git add docs/system_architecture.md
git commit -m "docs: update system architecture to reflect BT cleanup"
```

### Task 5: Final Verification

- [ ] **Step 1: Run all tests**

Run: `pytest tests/test_bt_nodes.py tests/test_fuzzy_match_fallback.py tests/test_bt_fallback.py`

- [ ] **Step 2: Check for any remaining references**

Run: `grep -r "selected_memories" .` and `grep -r "reply_photos" .`
