# Mem0 Cloud Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 remember 记忆层切换为托管版 `mem0` API，固定 `user_id="soj"`，并保持现有录音与 Groq 总结链路不变。

**Architecture:** 在 `StorageService` 下新增 `Mem0RememberStore`，通过 `mem0` 官方 Python SDK 调用托管平台的 `add/search/get_all`。记忆召回结果先做本地 JSON 提取，再交给现有 remember summary 提示词做自然语言整理。

**Tech Stack:** Python 3.13, mem0 Python SDK, requests, existing OpenAI-compatible Groq client, unittest

---

### Task 1: 增加后端配置与失败测试

**Files:**
- Modify: `tests/test_core_behaviors.py`
- Modify: `press_to_talk/storage/service.py`

- [ ] **Step 1: 写 `mem0` 配置与缺失配置的失败测试**

```python
def test_load_storage_config_accepts_mem0_backend(self) -> None:
    with patch.dict(
        core.os.environ,
        {
            "VOICE_ASSISTANT_DATA_BACKEND": "mem0",
            "MEM0_API_KEY": "test-key",
        },
        clear=False,
    ):
        config = core.build_storage_config(SimpleNamespace(data_backend="mem0", sqlite_path=Path("/tmp/x")))
        self.assertEqual(config.backend, "mem0")
```

- [ ] **Step 2: 运行目标测试，确认先失败**

Run: `uv run python -m unittest tests.test_core_behaviors.StorageServiceTests`
Expected: 出现与 `mem0` 配置或行为缺失相关的失败

- [ ] **Step 3: 增加 `StorageConfig` 的 `mem0` 字段与校验逻辑**

```python
mem0_api_key: str
mem0_user_id: str
```

- [ ] **Step 4: 重新运行目标测试，确认通过**

Run: `uv run python -m unittest tests.test_core_behaviors.StorageServiceTests`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_core_behaviors.py press_to_talk/storage/service.py
git commit -m "test: cover mem0 storage config"
```

### Task 2: 先写 remember store 的接口测试

**Files:**
- Modify: `tests/test_core_behaviors.py`
- Modify: `press_to_talk/storage/service.py`

- [ ] **Step 1: 为 `Mem0RememberStore` 写 add/find/list_recent 的测试**

```python
def test_mem0_store_add_uses_fixed_user_id(self) -> None:
    client = FakeMem0Client()
    store = service.Mem0RememberStore(client=client, user_id="soj")
    result = store.add(memory="护照在书房抽屉里", original_text="帮我记住护照在书房抽屉里")
    self.assertIn("已记录", result)
    self.assertEqual(client.add_calls[0]["user_id"], "soj")
```

- [ ] **Step 2: 运行这些测试，确认先失败**

Run: `uv run python -m unittest tests.test_core_behaviors.Mem0RememberStoreTests`
Expected: FAIL，提示 `Mem0RememberStore` 尚不存在或行为不符

- [ ] **Step 3: 实现 `Mem0RememberStore` 和 `StorageService.remember_store()` 分发**

```python
class Mem0RememberStore(BaseRememberStore):
    ...
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run python -m unittest tests.test_core_behaviors.Mem0RememberStoreTests`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_core_behaviors.py press_to_talk/storage/service.py
git commit -m "feat: add mem0 remember store"
```

### Task 3: 加入 JSON 提取与总结输入测试

**Files:**
- Modify: `tests/test_core_behaviors.py`
- Modify: `press_to_talk/core.py`

- [ ] **Step 1: 写 Mem0 JSON 提取测试**

```python
def test_extract_mem0_search_payload_keeps_key_fields(self) -> None:
    payload = {"results": [{"id": "m1", "memory": "护照在书房抽屉里", "score": 0.91}]}
    extracted = core.extract_mem0_summary_payload(payload)
    self.assertEqual(extracted["items"][0]["memory"], "护照在书房抽屉里")
```

- [ ] **Step 2: 运行目标测试，确认先失败**

Run: `uv run python -m unittest tests.test_core_behaviors.ThinkTagFilterTests`
Expected: FAIL，提示解析函数不存在

- [ ] **Step 3: 实现 Mem0 结果解析，并接入 remember summary 输入**

```python
parsed = extract_mem0_summary_payload(raw_json)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run python -m unittest tests.test_core_behaviors`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_core_behaviors.py press_to_talk/core.py
git commit -m "feat: parse mem0 recall payloads"
```

### Task 4: 文档与依赖

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`

- [ ] **Step 1: 更新依赖和 README**

```toml
"mem0ai>=..."
```

- [ ] **Step 2: 运行最小回归**

Run: `uv run python -m unittest tests.test_core_behaviors`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add pyproject.toml README.md
git commit -m "docs: document mem0 backend setup"
```

### Task 5: 最终验证

**Files:**
- Modify: `none`

- [ ] **Step 1: 跑核心单测**

Run: `uv run python -m unittest tests.test_core_behaviors`
Expected: PASS

- [ ] **Step 2: 跑文本链路 smoke test**

Run: `uv run press-to-talk --text-input "帮我记住护照在书房抽屉里" --no-tts`
Expected: 能进入 remember_add，并在缺少真实 key 时给出明确配置错误；有 key 时正常完成

- [ ] **Step 3: 提交最终实现**

```bash
git add pyproject.toml README.md press_to_talk/core.py press_to_talk/storage/service.py tests/test_core_behaviors.py
git commit -m "feat: integrate mem0 cloud memory backend"
```
