# Voice Assistant CI 检查上线标准审阅报告

**日期**: 2026-04-29  
**审阅对象**: `scripts/ci_check.py`  
**审阅目标**: 评估是否符合生产环境上线标准，并提供改进计划

---

## 1. 项目概述

### 1.1 架构概览

Voice Assistant 是一个基于 FastAPI 的语音助手系统，核心流程：

```
Audio Input → STT → Intent Extraction → Context Retrieval (Memory) → LLM Summary → TTS Output
```

### 1.2 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI 0.110+ |
| ORM | peewee 3.17+ (SQLite) |
| 认证 | OAuth2PasswordBearer + APIToken (自动创建) |
| 记忆系统 | SQLite FTS5 / mem0 (可选) |
| 部署 | Docker + docker-compose |
| 语音 | STT (Groq/本地) + TTS (qwen-tts) |

### 1.3 部署方式

- **Docker 镜像**: Python 3.13-slim 基础镜像
- **启动脚本**: `start.sh` (重建 FTS 索引 → 启动 sqlite_web → 启动 ptt-api)
- **数据持久化**: `/app/data` 卷挂载
- **端口**: 10031 (API), 8080 (sqlite_web)

---

## 2. 当前 CI 检查覆盖分析

### 2.1 ci_check.py 当前检查项（共 7 项）

#### ✅ 检查项 1: 环境清理与准备
- **内容**: 清理并创建临时测试目录 `tmp_ci_data`
- **评估**: 基础但必要，确保测试环境干净
- **覆盖**: 部分覆盖

#### ✅ 检查项 2: 依赖检查
- **内容**: 检查 `uv` 是否安装
- **评估**: 只检查了 uv，未检查其他系统依赖（git, cmake, portaudio 等）
- **覆盖**: 不足

#### ✅ 检查项 3: 核心单元测试
- **内容**: 运行 `test_api_query_robustness.py` 和 `test_smoke_check.py`
- **评估**: 只覆盖了部分测试文件，项目中共有 18 个测试文件，仅运行了 2 个
- **覆盖**: 严重不足

**项目中所有测试文件**:
```
tests/test_api_query_robustness.py       ✅ 已覆盖
tests/test_smoke_check.py                ✅ 已覆盖
tests/test_core_behaviors.py             ❌ 未覆盖
tests/test_bt_nodes.py                   ❌ 未覆盖
tests/test_execution_modes.py            ❌ 未覆盖
tests/test_intent_time_parsing.py        ❌ 未覆盖
tests/test_multi_user_robustness.py      ❌ 未覆盖 (仅在 ci_check 中手动测试了部分)
tests/test_storage_cli.py                ❌ 未覆盖
tests/test_storage_search_toggles.py     ❌ 未覆盖
tests/test_photo_storage.py              ❌ 未覆盖
tests/test_date_range_query.py           ❌ 未覆盖
tests/test_fuzzy_match_fallback.py      ❌ 未覆盖
tests/test_gui_events.py                 ❌ 未覆盖
tests/test_bt_fallback.py                ❌ 未覆盖
tests/test_bt_base.py                    ❌ 未覆盖
tests/test_ollama_embedding.py           ❌ 未覆盖
tests/test_api_production_vibe.py        ❌ 未覆盖
tests/test_run_gui_script.py             ❌ 未覆盖
tests/mem0_e2e.py (E2E)                ❌ 未覆盖
```

#### ✅ 检查项 4: 数据库自动初始化与 FTS5 支持验证
- **内容**: 通过 CLI 写入一条记录，触发表创建
- **评估**: 基础验证，但未验证 FTS 索引是否正常工作
- **覆盖**: 部分覆盖

#### ✅ 检查项 5: 图片过滤逻辑测试
- **内容**: 运行 `test_api_query_robustness.py::test_api_images_filtering_logic`
- **评估**: 只测试了图片过滤，未测试图片上传、存储、检索等完整流程
- **覆盖**: 部分覆盖

#### ✅ 检查项 6: 端到端多用户隔离测试
- **内容**: 手动测试 user_a 和 user_b 的数据隔离
- **评估**: 测试了基础隔离，但未覆盖所有 API 端点的隔离性
- **覆盖**: 部分覆盖

#### ✅ 检查项 7: 冒烟测试
- **内容**: 运行 `test_smoke_check.py`
- **评估**: 与检查项 3 重复
- **覆盖**: 重复

---

## 3. 上线标准差距分析

### 3.1 P0 - 必须有的上线前检查（缺失 7 项）

| # | 检查项 | 当前状态 | 风险等级 |
|---|--------|----------|----------|
| P0-1 | **API 端点覆盖率测试** | ❌ 缺失 | 🔴 高风险 |
| P0-2 | **认证/授权测试** | ❌ 缺失 | 🔴 高风险 |
| P0-3 | **错误处理测试** | ❌ 缺失 | 🔴 高风险 |
| P0-4 | **Docker 构建验证** | ❌ 缺失 | 🔴 高风险 |
| P0-5 | **配置验证** | ❌ 缺失 | 🔴 高风险 |
| P0-6 | **FTS 重建验证** | ❌ 缺失 | 🟡 中风险 |
| P0-7 | **数据库连接测试** | ❌ 缺失 | 🔴 高风险 |

**详细说明**:

**P0-1: API 端点覆盖率测试**
- **当前**: 只测试了部分 API（通过 test_smoke_check.py）
- **缺失**: 未测试所有 API 端点（/v1/query, /v1/history, /v1/memories 等）
- **风险**: 上线后可能出现 API 端点不可用或返回错误格式
- **建议**: 添加 API 端点集成测试，覆盖所有路由

**P0-2: 认证/授权测试**
- **当前**: ci_check.py 中使用了固定的 `PTT_USER_ID=ci_admin`，未测试认证失败场景
- **缺失**: 
  - 无效 token 测试
  - 过期 token 测试
  - 权限隔离测试（不同用户不能访问对方数据）
  - 未认证请求被拒绝
- **风险**: 认证机制可能存在漏洞，导致数据泄露
- **建议**: 添加认证失败场景的测试用例

**P0-3: 错误处理测试**
- **当前**: 未测试 API 的错误处理
- **缺失**:
  - 无效输入（空查询、超长查询等）
  - 数据库连接失败
  - 外部服务失败（STT/TTS/LLM）
  - 异常情况的 HTTP 状态码
- **风险**: 生产环境可能出现未处理的异常，导致服务崩溃或信息泄露
- **建议**: 添加错误处理测试用例

**P0-4: Docker 构建验证**
- **当前**: ci_check.py 在本地环境运行，未测试 Docker 构建
- **缺失**: 
  - Docker 镜像构建是否成功
  - 所有依赖是否在镜像中正确安装
  - 启动脚本是否正常工作
  - 端口映射是否正确
- **风险**: Docker 镜像可能存在问题，导致部署失败
- **建议**: 添加 `docker build` 和 `docker compose up` 测试

**P0-5: 配置验证**
- **当前**: 未验证必需的环境变量和配置文件
- **缺失**:
  - OPENAI_API_KEY / LLM_API_KEY 是否存在
  - workflow_config.json 是否有效
  - intent_extractor_config.json 是否有效
  - 数据库连接路径是否可写
- **风险**: 配置缺失导致服务启动失败
- **建议**: 添加配置验证步骤

**P0-6: FTS 重建验证**
- **当前**: start.sh 中有 `uv run ptt-storage memory rebuild-fts`，但未验证是否成功
- **缺失**: 
  - FTS 重建后索引是否正常
  - 搜索功能是否可用
  - 重建失败时的错误处理
- **风险**: FTS 索引损坏导致搜索功能失效
- **建议**: 添加 FTS 重建后的验证测试

**P0-7: 数据库连接测试**
- **当前**: 只测试了写入一条记录
- **缺失**:
  - 数据库连接是否正常
  - 表结构是否正确
  - 索引是否存在
  - 并发访问测试
- **风险**: 数据库问题导致服务不可用
- **建议**: 添加数据库连接和健康检查

### 3.2 P1 - 应该有的上线前检查（缺失 5 项）

| # | 检查项 | 当前状态 | 优先级 |
|---|--------|----------|--------|
| P1-1 | **安全扫描** | ❌ 缺失 | 应该 |
| P1-2 | **日志配置验证** | ❌ 缺失 | 应该 |
| P1-3 | **API 响应格式验证** | ❌ 缺失 | 应该 |
| P1-4 | **迁移脚本测试** | ❌ 缺失 | 应该 |
| P1-5 | **Mem0 集成测试** | ❌ 缺失 | 应该（可选依赖）|

**详细说明**:

**P1-1: 安全扫描**
- **建议**: 使用 `pip-audit` 或 `safety` 扫描依赖漏洞
- **工具**: `uv pip audit` 或 `safety check`

**P1-2: 日志配置验证**
- **建议**: 验证日志是否正常输出、格式是否正确、敏感信息是否被脱敏

**P1-3: API 响应格式验证**
- **建议**: 验证所有 API 返回的格式是否符合 OpenAPI 规范

**P1-4: 迁移脚本测试**
- **当前**: 有 `migrate_v2_peewee.py` 和 `migrate_mem0_app_id.py`
- **建议**: 测试迁移脚本是否能正确执行

**P1-5: Mem0 集成测试**
- **当前**: `tests/mem0_e2e.py` 存在但需要 `PTT_RUN_E2E=1` 环境变量
- **建议**: 在 CI 中条件性运行 Mem0 测试

### 3.3 P2 - 可以有的上线前检查（缺失 3 项）

| # | 检查项 | 当前状态 | 优先级 |
|---|--------|----------|--------|
| P2-1 | **性能基准测试** | ❌ 缺失 | 可选 |
| P2-2 | **并发/负载测试** | ❌ 缺失 | 可选 |
| P2-3 | **资源使用测试** | ❌ 缺失 | 可选 |

---

## 4. 改进方案

### 方案 A: 增强 ci_check.py（推荐）

**思路**: 在现有 `ci_check.py` 基础上添加缺失的检查项。

**优点**:
- 无需引入新工具
- 保持现有工作流程
- 易于理解和维护

**缺点**:
- 脚本会越来越长
- 需要手动维护所有检查逻辑

**实施步骤**:
1. 添加 P0 检查项（见 Section 5.1）
2. 添加 P1 检查项（见 Section 5.2）
3. 重构为模块化结构（可选）

### 方案 B: 引入专业 CI 工具（如 pytest + GitHub Actions）

**思路**: 使用 GitHub Actions 或类似 CI/CD 工具，配合 pytest 进行系统化测试。

**优点**:
- 标准化的 CI/CD 流程
- 更好的并行化和缓存
- 集成代码覆盖、安全扫描等

**缺点**:
- 需要学习新工具
- 可能需要调整项目结构
- 对于小型项目可能过重

**实施步骤**:
1. 创建 `.github/workflows/ci.yml`
2. 将 ci_check.py 的逻辑迁移到 pytest fixtures
3. 配置 GitHub Actions  runners

### 方案 C: 混合方案（适用于当前项目）

**思路**: 保留 ci_check.py 作为快速本地检查工具，同时引入 Makefile 或 poe tasks 来组织检查流程。

**优点**:
- 兼顾本地快速检查和 CI 集成
- 可以逐步迁移到更专业的工具
- 灵活可扩展

**缺点**:
- 需要维护两套检查逻辑（直到完全迁移）

**实施步骤**:
1. 重构 ci_check.py，将检查项模块化
2. 添加 Makefile 或 poe tasks 来调用检查项
3. 可选：逐步引入 GitHub Actions

---

## 5. 实施计划（方案 A - 增强 ci_check.py）

### 5.1 Phase 1: P0 检查项（本周完成）

#### Step 1.1: 添加 API 端点覆盖率测试

**目标**: 确保所有 API 端点都被测试到

**实施**:
```python
# 在 ci_check.py 中添加
def test_api_endpoints(base_url="http://localhost:10031"):
    """测试所有 API 端点"""
    endpoints = [
        ("/v1/query", "POST", {"query": "test"}),
        ("/v1/history", "POST", {}),
        ("/v1/memories", "POST", {}),
    ]
    for path, method, data in endpoints:
        # 测试未认证请求（应该返回 401）
        # 测试认证请求（应该返回 200 或预期响应）
        pass
```

**验证**: 运行 `uv run pytest tests/ -v --collect-only` 查看覆盖的测试

#### Step 1.2: 添加认证/授权测试

**目标**: 测试认证失败场景

**实施**:
```python
def test_auth_failures():
    """测试认证失败场景"""
    # 无效 token
    # 过期 token
    # 未认证请求
    pass
```

#### Step 1.3: 添加错误处理测试

**目标**: 测试 API 的错误处理

**实施**:
```python
def test_error_handling():
    """测试错误处理"""
    # 无效输入
    # 数据库错误
    # 外部服务错误
    pass
```

#### Step 1.4: 添加 Docker 构建验证

**目标**: 验证 Docker 镜像能否成功构建

**实施**:
```python
def test_docker_build():
    """测试 Docker 构建"""
    run_command("docker build -t voice-assistant-test .")
    run_command("docker run --rm voice-assistant-test uv run ptt-storage --help")
```

#### Step 1.5: 添加配置验证

**目标**: 验证所有必需的环境变量和配置文件

**实施**:
```python
def test_config_validation():
    """验证配置"""
    required_env_vars = ["OPENAI_API_KEY", "PTT_API_KEY"]
    for var in required_env_vars:
        if not os.environ.get(var):
            log(f"Missing env var: {var}")
            return False
    # 验证 workflow_config.json
    # 验证 intent_extractor_config.json
    return True
```

#### Step 1.6: 添加 FTS 重建验证

**目标**: 验证 FTS 索引重建后是否正常工作

**实施**:
```python
def test_fts_rebuild():
    """测试 FTS 重建"""
    # 重建 FTS
    run_command("uv run ptt-storage memory rebuild-fts")
    # 验证搜索功能
    result = run_command("uv run ptt-storage memory search --query 'test'")
    # 验证结果
    pass
```

#### Step 1.7: 添加数据库连接测试

**目标**: 验证数据库连接和健康状态

**实施**:
```python
def test_database_connection():
    """测试数据库连接"""
    # 连接数据库
    # 执行简单查询
    # 验证表结构
    pass
```

### 5.2 Phase 2: P1 检查项（下周完成）

#### Step 2.1: 添加安全扫描

**实施**:
```python
def test_security_scan():
    """安全扫描"""
    run_command("uv pip audit")  # 或使用 safety check
```

#### Step 2.2: 添加日志配置验证

**实施**:
```python
def test_logging_config():
    """验证日志配置"""
    # 启动服务，检查日志输出
    pass
```

#### Step 2.3: 添加 API 响应格式验证

**实施**:
```python
def test_api_response_format():
    """验证 API 响应格式"""
    # 调用 API，验证响应符合 OpenAPI 规范
    pass
```

#### Step 2.4: 添加迁移脚本测试

**实施**:
```python
def test_migration_scripts():
    """测试迁移脚本"""
    # 运行迁移脚本
    # 验证数据是否正确迁移
    pass
```

#### Step 2.5: 添加 Mem0 集成测试

**实施**:
```python
def test_mem0_integration():
    """测试 Mem0 集成（条件性）"""
    if os.environ.get("PTT_RUN_E2E"):
        run_command("uv run python tests/mem0_e2e.py")
```

### 5.3 Phase 3: P2 检查项（可选）

#### Step 3.1: 添加性能基准测试

**实施**: 使用 `pytest-benchmark` 或类似工具

#### Step 3.2: 添加并发/负载测试

**实施**: 使用 `locust` 或 `wrk` 进行简单负载测试

#### Step 3.3: 添加资源使用测试

**实施**: 监控内存、CPU 使用情况

---

## 6. 改进后的 ci_check.py 结构（伪代码）

```python
#!/usr/bin/env python3
"""Voice Assistant CI 检查脚本 - 上线前完整验证"""

import os
import sys
import subprocess
from pathlib import Path

class CIChecker:
    """CI 检查器"""
    
    def __init__(self):
        self.workspace = Path.cwd()
        self.test_data_dir = self.workspace / "tmp_ci_data"
        self.ci_env = self._prepare_env()
        self.results = []
    
    def _prepare_env(self):
        """准备 CI 环境变量"""
        env = os.environ.copy()
        env["PTT_REMEMBER_DB_PATH"] = str(self.test_data_dir / "ci_store.sqlite3")
        env["PTT_WORKSPACE_ROOT"] = str(self.test_data_dir)
        env["PTT_USER_ID"] = "ci_admin"
        return env
    
    # P0 检查项
    def check_dependencies(self):
        """检查依赖"""
        pass
    
    def check_api_endpoints_coverage(self):
        """检查 API 端点覆盖率"""
        pass
    
    def check_auth_failures(self):
        """检查认证失败场景"""
        pass
    
    def check_error_handling(self):
        """检查错误处理"""
        pass
    
    def check_docker_build(self):
        """检查 Docker 构建"""
        pass
    
    def check_config_validation(self):
        """检查配置验证"""
        pass
    
    def check_fts_rebuild(self):
        """检查 FTS 重建"""
        pass
    
    def check_database_connection(self):
        """检查数据库连接"""
        pass
    
    # P1 检查项
    def check_security_scan(self):
        """安全扫描"""
        pass
    
    def check_logging_config(self):
        """检查日志配置"""
        pass
    
    def check_api_response_format(self):
        """检查 API 响应格式"""
        pass
    
    def check_migration_scripts(self):
        """检查迁移脚本"""
        pass
    
    def check_mem0_integration(self):
        """检查 Mem0 集成"""
        pass
    
    # P2 检查项（可选）
    def check_performance_benchmark(self):
        """性能基准测试"""
        pass
    
    def run_all(self, phase="all"):
        """运行所有检查"""
        pass

if __name__ == "__main__":
    checker = CIChecker()
    sys.exit(checker.run_all())
```

---

## 7. 总结与建议

### 7.1 当前状态

**ci_check.py 符合度评分**: 4/10

| 维度 | 评分 | 说明 |
|------|------|------|
| 测试覆盖率 | 3/10 | 只覆盖了 2/18 测试文件 |
| 安全检查 | 0/10 | 无任何安全扫描 |
| 配置验证 | 1/10 | 只验证了基础环境变量 |
| 部署验证 | 2/10 | 未验证 Docker 构建 |
| 错误处理 | 0/10 | 未测试错误处理 |
| 认证授权 | 2/10 | 基础多用户测试，未覆盖认证失败 |

### 7.2 给 ChatGPT 的审阅问题

请 ChatGPT 审阅以下问题：

1. **优先级判断**: 在 P0 缺失的 7 个检查项中，你认为哪些是最关键的？请排序。
2. **方案选择**: 对于当前项目规模，你推荐方案 A、B 还是 C？理由是什么？
3. **测试策略**: 如何平衡单元测试、集成测试和端到端测试的比例？
4. **CI 工具**: 如果引入 GitHub Actions，你推荐哪些 actions 和配置？
5. **安全扫描**: 除了依赖扫描，还需要哪些安全检查？（如代码扫描、镜像扫描等）
6. **性能基准**: 对于语音助手这类应用，应该关注哪些性能指标？
7. **监控告警**: 上线前是否需要配置监控和告警？如果需要，推荐哪些工具？

### 7.3 下一步行动

1. **等待 ChatGPT 审阅反馈**
2. **根据反馈调整实施计划**
3. **实施 Phase 1 (P0 检查项)**
4. **测试改进后的 ci_check.py**
5. **提交 PR 并请求代码审查**

---

**报告生成时间**: 2026-04-29  
**报告版本**: v1.0  
**生成者**: Claude (Tencent/hy3-preview:free)  
**审阅目标**: ChatGPT
