# 执行层行为树重构 (Execution Layer BT Refactor) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `execution` 层逻辑重构为基于行为树（Behavior Tree）的结构，以提高稳定性和可维护性，彻底消除复杂的 `if-else` 嵌套。

**Architecture:** 采用“黑板”模式共享上下文，通过组合节点（Selector/Sequence）和叶子节点（Action/Condition）构建统一的主执行树。

**Tech Stack:** Python 3.13, OpenAI SDK (compatible LLM), SQLite FTS5.

---

### Task 1: 行为树基础架构实现

**Files:**
- Create: `press_to_talk/execution/bt/base.py`
- Test: `tests/test_bt_base.py`

- [ ] **Step 1: 创建行为树基础类和枚举**

```python
from enum import Enum, auto
from typing import List, Optional, Any
from dataclasses import dataclass, field

class Status(Enum):
    SUCCESS = auto()
    FAILURE = auto()
    RUNNING = auto()

@dataclass
class Blackboard:
    transcript: str
    cfg: Any
    mode: str = "database"
    intent: dict = field(default_factory=dict)
    memories: list = field(default_factory=list)
    reply: Optional[str] = None
    error: Optional[str] = None

class Node:
    def tick(self, bb: Blackboard) -> Status:
        raise NotImplementedError

class Composite(Node):
    def __init__(self, children: List[Node]):
        self.children = children

class Sequence(Composite):
    def tick(self, bb: Blackboard) -> Status:
        for child in self.children:
            status = child.tick(bb)
            if status != Status.SUCCESS:
                return status
        return Status.SUCCESS

class Selector(Composite):
    def tick(self, bb: Blackboard) -> Status:
        for child in self.children:
            status = child.tick(bb)
            if status != Status.FAILURE:
                return status
        return Status.FAILURE
```

- [ ] **Step 2: 编写基础架构测试**

```python
from press_to_talk.execution.bt.base import Blackboard, Sequence, Selector, Node, Status

class SuccessNode(Node):
    def tick(self, bb): return Status.SUCCESS

class FailureNode(Node):
    def tick(self, bb): return Status.FAILURE

def test_bt_logic():
    bb = Blackboard(transcript="test", cfg=None)
    
    seq = Sequence([SuccessNode(), SuccessNode()])
    assert seq.tick(bb) == Status.SUCCESS
    
    seq_fail = Sequence([SuccessNode(), FailureNode()])
    assert seq_fail.tick(bb) == Status.FAILURE
    
    sel = Selector([FailureNode(), SuccessNode()])
    assert sel.tick(bb) == Status.SUCCESS
    
    sel_fail = Selector([FailureNode(), FailureNode()])
    assert sel_fail.tick(bb) == Status.FAILURE
```

- [ ] **Step 3: 运行测试并提交**

Run: `pytest tests/test_bt_base.py`
Expected: PASS

```bash
git add press_to_talk/execution/bt/base.py tests/test_bt_base.py
git commit -m "feat(bt): add behavior tree core infrastructure"
```

---

### Task 2: 条件节点与黑板扩展实现

**Files:**
- Create: `press_to_talk/execution/bt/nodes.py`
- Modify: `press_to_talk/execution/bt/base.py`

- [ ] **Step 1: 实现基础动作和条件节点类**

```python
# In press_to_talk/execution/bt/nodes.py
from .base import Node, Status, Blackboard

class Condition(Node):
    pass

class Action(Node):
    pass

class IsRecordIntent(Condition):
    def tick(self, bb: Blackboard) -> Status:
        if bb.intent.get("intent") == "record":
            return Status.SUCCESS
        return Status.FAILURE

class HasMemoryHits(Condition):
    def tick(self, bb: Blackboard) -> Status:
        if bb.memories:
            return Status.SUCCESS
        return Status.FAILURE

class IsChatMode(Condition):
    def tick(self, bb: Blackboard) -> Status:
        if bb.mode == "memory-chat":
            return Status.SUCCESS
        return Status.FAILURE

class IsHermesMode(Condition):
    def tick(self, bb: Blackboard) -> Status:
        if bb.mode == "hermes":
            return Status.SUCCESS
        return Status.FAILURE
```

- [ ] **Step 2: 提交代码**

```bash
git add press_to_talk/execution/bt/nodes.py
git commit -m "feat(bt): implement basic condition nodes"
```

---

### Task 3: 核心动作节点实现 (Intent, Search, Summarize)

**Files:**
- Modify: `press_to_talk/execution/bt/nodes.py`

- [ ] **Step 1: 实现 ExtractIntentAction 节点**

```python
# In press_to_talk/execution/bt/nodes.py
from ...agent.agent import OpenAICompatibleAgent
from ...utils.logging import log

class ExtractIntentAction(Action):
    def tick(self, bb: Blackboard) -> Status:
        try:
            # 复用现有的 OpenAICompatibleAgent 逻辑
            agent = OpenAICompatibleAgent(bb.cfg)
            # 这里需要把 agent 的 _extract_intent_payload 暴露或包装
            import asyncio
            bb.intent = asyncio.run(agent._extract_intent_payload(bb.transcript))
            return Status.SUCCESS
        except Exception as e:
            log(f"BT Action Error (ExtractIntent): {e}", level="error")
            bb.error = str(e)
            return Status.FAILURE
```

- [ ] **Step 2: 实现 ExecuteSearchAction 节点**

```python
# In press_to_talk/execution/bt/nodes.py
from ...models.history import build_storage_config
from ...storage import StorageService

class ExecuteSearchAction(Action):
    def tick(self, bb: Blackboard) -> Status:
        try:
            storage = StorageService(build_storage_config(bb.cfg))
            remember_store = storage.remember_store()
            raw = remember_store.find(query=bb.transcript)
            extracted = remember_store.extract_summary_items(raw)
            items = extracted.get("items", []) if isinstance(extracted, dict) else []
            bb.memories = [item for item in items if isinstance(item, dict)]
            return Status.SUCCESS
        except Exception as e:
            log(f"BT Action Error (ExecuteSearch): {e}", level="error")
            return Status.FAILURE
```

- [ ] **Step 3: 实现 LLMSummarizeAction 和 LLMChatFallbackAction**

```python
# In press_to_talk/execution/bt/nodes.py
from ..memory_chat import MemoryChatExecutionRunner

class LLMSummarizeAction(Action):
    def tick(self, bb: Blackboard) -> Status:
        try:
            runner = MemoryChatExecutionRunner(bb.cfg)
            memory_context = runner._memory_context(bb.transcript) # 复用逻辑
            messages = runner._build_messages(
                bb.transcript,
                intent=bb.intent or {"intent": "find", "notes": ""},
                memory_context=memory_context or "没有命中相关记忆。"
            )
            response = runner.client.chat.completions.create(
                model=runner.summary_model,
                messages=messages,
                temperature=0.2,
            )
            bb.reply = response.choices[0].message.content.strip()
            return Status.SUCCESS
        except Exception as e:
            log(f"BT Action Error (LLMSummarize): {e}", level="error")
            return Status.FAILURE

class LLMChatFallbackAction(Action):
    # 与 Summarize 类似，但不传记忆 context
    def tick(self, bb: Blackboard) -> Status:
        # 实现逻辑与 SummarizeAction 类似，但 memory_context 设为 "没有命中相关记忆。"
        # 为了简洁，可以合并为一个带有参数的 Action 类
        pass
```

- [ ] **Step 4: 实现 ExecuteRecordAction**

```python
# In press_to_talk/execution/bt/nodes.py
from ..intent import IntentExecutionRunner

class ExecuteRecordAction(Action):
    def tick(self, bb: Blackboard) -> Status:
        try:
            runner = IntentExecutionRunner(bb.cfg)
            bb.reply = runner.run(bb.transcript)
            return Status.SUCCESS
        except Exception as e:
            log(f"BT Action Error (ExecuteRecord): {e}", level="error")
            return Status.FAILURE
```

- [ ] **Step 5: 提交动作节点**

```bash
git add press_to_talk/execution/bt/nodes.py
git commit -m "feat(bt): implement core action nodes"
```

---

### Task 4: 树的组装与执行层集成

**Files:**
- Create: `press_to_talk/execution/bt/builder.py`
- Modify: `press_to_talk/execution/__init__.py`

- [ ] **Step 1: 创建树组装器**

```python
# In press_to_talk/execution/bt/builder.py
from .base import Selector, Sequence, Blackboard
from .nodes import (
    ExtractIntentAction, IsRecordIntent, ExecuteRecordAction,
    ExecuteSearchAction, HasMemoryHits, LLMSummarizeAction,
    IsChatMode, LLMChatFallbackAction, IsHermesMode
)

def build_master_tree():
    return Selector([
        # 1. 记录分支
        Sequence([
            ExtractIntentAction(),
            IsRecordIntent(),
            ExecuteRecordAction()
        ]),
        # 2. 查询与总结分支
        Sequence([
            ExecuteSearchAction(),
            HasMemoryHits(),
            LLMSummarizeAction()
        ]),
        # 3. Chat 模式兜底
        Sequence([
            IsChatMode(),
            LLMChatFallbackAction()
        ]),
        # 4. 彻底没招了
        # (可选：添加一个默认回复节点)
    ])
```

- [ ] **Step 2: 在执行层入口调用行为树**

```python
# In press_to_talk/execution/__init__.py
from .bt.base import Blackboard
from .bt.builder import build_master_tree
from .resolver import resolve_execution_mode

def execute_transcript(cfg: Any, transcript: str) -> str:
    mode = resolve_execution_mode(cfg)
    bb = Blackboard(transcript=transcript, cfg=cfg, mode=mode)
    tree = build_master_tree()
    
    status = tree.tick(bb)
    
    if bb.reply:
        return bb.reply
    if bb.error:
        return f"执行出错: {bb.error}"
    return "没有找到匹配的记忆信息。"
```

- [ ] **Step 3: 运行 E2E 测试验证逻辑**

Run: `uv run press-to-talk --text-input "杜甫是谁" --no-tts`
Expected: 正常通过行为树返回结果。

- [ ] **Step 4: 提交并清理**

```bash
git add press_to_talk/execution/bt/builder.py press_to_talk/execution/__init__.py
git commit -m "feat(bt): integrate behavior tree into execution flow"
```
