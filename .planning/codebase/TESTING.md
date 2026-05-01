# 测试现状 (TESTING.md)

## 测试套件
- **测试框架**: `pytest`
- **任务运行器**: `poe test`

## 覆盖范围
- **行为树**: `test_bt_nodes.py`, `test_bt_base.py` 验证各节点及回退逻辑。
- **意图解析**: `test_intent_time_parsing.py` 包含大量时间提取及意图理解的测试用例。
- **存储层**: `test_storage_cli.py`, `test_database_connection.py` 确保多后端连接正常。
- **API/E2E**: `test_api_endpoints_coverage.py`, `mem0_e2e.py` 进行全链路覆盖。
- **稳定性**: 包含 `test_auth_failures.py`, `test_multi_user_robustness.py` 等边缘情况测试。

## 性能约束
- **超时机制**: `pytest.ini` 配置了单个测试 30 秒自动超时的硬限制。
- **实时探测**: 提供 `live_llm_probe.py` 用于在线模型能力的冒烟测试。
