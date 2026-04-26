# 存储层检索开关与分数规范化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `SQLiteFTS5RememberStore` 中实现检索开关逻辑，并确保所有返回的记忆项都包含 float 类型的值。

**Architecture:** 
- 修改 `SQLiteFTS5RememberStore` 的初始化逻辑，接受新的开关配置。
- 在 `find` 方法中根据开关条件执行检索。
- 规范化 `extract_sqlite_summary_payload` 和 `find` 的输出结构。

**Tech Stack:** Python, SQLite, Peewee

---

### Task 1: 更新构造函数与配置加载

**Files:**
- Modify: `press_to_talk/storage/providers/sqlite_fts.py`

- [ ] **Step 1: 修改 `__init__` 方法接收开关参数**

```python
    def __init__(
        self,
        *,
        user_id: str,
        db_path: str | Path,
        max_results: int = 3,
        keyword_rewriter: KeywordRewriter | None = None,
        embedding_client: EmbeddingClient | None = None,
        embedding_model: str = "",
        embedding_max_results: int = 5,
        embedding_min_score: float = 0.45,
        embedding_context_min_score: float = 0.55,
        keyword_search_enabled: bool = True,    # 新增
        semantic_search_enabled: bool = True,   # 新增
    ) -> None:
        # ... 现有初始化 ...
        self.keyword_search_enabled = keyword_search_enabled
        self.semantic_search_enabled = semantic_search_enabled
```

- [ ] **Step 2: 修改 `from_config` 方法传递开关**

```python
    @classmethod
    def from_config(cls, config: StorageConfig, **kwargs) -> SQLiteFTS5RememberStore:
        return cls(
            user_id=config.user_id,
            db_path=config.remember_db_path,
            max_results=config.remember_max_results,
            keyword_rewriter=kwargs.get("keyword_rewriter"),
            embedding_client=kwargs.get("embedding_client"),
            embedding_model=config.embedding_model,
            embedding_max_results=config.embedding_max_results,
            embedding_min_score=config.embedding_min_score,
            embedding_context_min_score=config.embedding_context_min_score,
            keyword_search_enabled=config.keyword_search_enabled,    # 新增
            semantic_search_enabled=config.semantic_search_enabled,  # 新增
        )
```

- [ ] **Step 3: 运行测试确保初始化正常**

### Task 2: 实现 `find` 方法的开关逻辑与分数规范化

**Files:**
- Modify: `press_to_talk/storage/providers/sqlite_fts.py`

- [ ] **Step 1: 在 `find` 方法中添加开关判断**

```python
    def find(self, ...):
        # ... 日期范围查询保持不变 ...

        results = []
        
        # 关键词搜索分支
        if self.keyword_search_enabled:
            # 原有的关键词搜索逻辑 ...
            # 确保结果包含 score: float
            # results.extend(...)

        # 语义搜索分支
        if self.semantic_search_enabled and self._embedding_enabled():
            # 原有的语义搜索逻辑 ...
            # 确保结果包含 score: float
            # 合并逻辑处理 ...
            
        return json.dumps({"results": results}, ensure_ascii=False)
```

- [ ] **Step 2: 确保所有路径返回的结果都包含 `score` 且为 float**

### Task 3: 更新提取函数

**Files:**
- Modify: `press_to_talk/storage/providers/sqlite_fts.py`

- [ ] **Step 1: 修改 `extract_sqlite_summary_payload` 确保 score 存在**

```python
        extracted: dict[str, Any] = {
            "id": str(item.get("id") or "").strip(),
            "memory": memory,
            "score": float(item.get("score")) if item.get("score") is not None else 0.0,
            # ... 其他字段 ...
        }
```

### Task 4: 验证与提交

- [ ] **Step 1: 编写测试脚本验证开关逻辑**
- [ ] **Step 2: 验证返回的 JSON 包含 score 字段**
- [ ] **Step 3: 提交代码**
