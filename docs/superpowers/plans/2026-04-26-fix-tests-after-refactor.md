# 修复由 API 重构和 Photo 闭环引起的测试失败计划

**Goal:** 修复 `pytest` 中的失败用例，使其适应新的 `PhotoAttachment` 模型和 `ExecutionResult` 返回类型。

**Architecture:** 
1. **API Tests**: 更新 `tests/test_api_query_robustness.py` 中的请求 Payload 格式。
2. **Core Behavior Tests**: 更新 `tests/test_core_behaviors.py` 中对提示词内容和字典结构的断言。
3. **Execution Tests**: 适配 `execute_transcript_async` 返回的 `ExecutionResult` 对象。
4. **Mocking/Data Fixes**: 确保测试中的字典包含新增的 `photo_path` 字段。

---

### Task 1: 修复 API 鲁棒性测试

**Files:**
- Modify: `tests/test_api_query_robustness.py`

- [ ] **Step 1: 更新包含 photo 的 Payload 格式**
- [ ] **Step 2: 修复状态码校验**

### Task 2: 修复核心行为测试

**Files:**
- Modify: `tests/test_core_behaviors.py`

- [ ] **Step 1: 更新对 `remember_summary` 提示词中 `[ID: ...]` 的匹配断言**
- [ ] **Step 2: 在对比字典时考虑新增的 `photo_path` 字段**

### Task 3: 修复执行模式测试

**Files:**
- Modify: `tests/test_execution_modes.py`
- Modify: `tests/test_multi_user_robustness.py`

- [ ] **Step 1: 适配 `execute_transcript_async` 返回的对象属性访问**

### Task 4: 修复 GUI 事件/存储 CLI 测试

**Files:**
- Modify: `tests/test_gui_events.py`

- [ ] **Step 1: 更新字典预期值以包含 `photo_path`**

### Task 5: 验证所有测试通过

- [ ] **Step 1: 运行所有测试并确保 PASS**
