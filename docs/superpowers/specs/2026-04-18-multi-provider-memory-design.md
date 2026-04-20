# Multi-Provider Memory Design

## 背景
当前记忆检索链路已经同时支持两类后端能力：
- `mem0` 云端记忆
- 本地 `sqlite_fts5 + query rewrite + embedding` 混合检索

但 agent 总结前的结果提取仍然复用 `extract_mem0_summary_payload()`。这导致：
- `mem0` 专用的 `min_score / max_items` 规则误作用到本地 SQLite 结果
- provider 边界不清，agent 需要知道 provider 的返回形状和过滤规则
- 新增 provider 时，agent 层会继续膨胀

实际故障已经出现：本地 SQLite 检索明明返回了“拔智齿”相关记录，但由于被 `mem0.min_score` 过滤，最终回复错误地变成“没有找到匹配的记忆信息”。

## 目标
- 把记忆能力拆成真正的多 provider 架构
- `mem0` 与 `sqlite_fts5` 各自管理自己的检索结果提取与阈值逻辑
- agent 层不再直接调用 `mem0` 专用逻辑
- 保持现有外部接口稳定：`StorageService`、`press_to_talk.storage.cli_app`、`remember_find` 调用方式不变
- 保持本地录音启动体验不变，不给录音前路径增加额外初始化

## 非目标
- 不重写 CLI 协议
- 不改 history store 结构
- 不引入 ORM 或额外服务进程
- 不在本轮重做整套 storage 模块拆分，只聚焦记忆 provider 边界

## 方案对比
### 方案 A：继续在 agent 层按 backend 分支
优点：改动最小。
缺点：agent 继续知道 provider 细节，耦合不降反升。

### 方案 B：标准 provider 化（采用）
优点：
- provider 自己负责检索结果提取和 summary items 生成
- agent 只消费统一结构
- 未来新增 provider 时不需要再改 agent 主逻辑

缺点：
- 需要移动部分现有代码并补测试

### 方案 C：完整 repository/provider/facade 全量重构
优点：最整齐。
缺点：本轮范围过大，风险高。

## 目标结构
- `press_to_talk/storage/providers/base.py`
  - 定义统一协议：
    - `add()`
    - `find()`
    - `delete()`
    - `list_all()`
    - `extract_summary_items(raw_payload)`
- `press_to_talk/storage/providers/mem0.py`
  - `Mem0RememberStore`
  - `mem0` 专用 payload 提取、分数阈值、条数限制
- `press_to_talk/storage/providers/sqlite_fts.py`
  - `SQLiteFTS5RememberStore`
  - 本地 `FTS + embedding` 检索
  - 本地 provider 专用 summary item 提取，不再读取 `mem0.min_score`
- `press_to_talk/storage/providers/__init__.py`
  - provider 导出
- `press_to_talk/storage/service.py`
  - 仅负责配置读取、provider 装配与切换
- `press_to_talk/agent/agent.py`
  - 不再直接调用 `extract_mem0_summary_payload()`
  - 改为调用当前 remember provider 的 `extract_summary_items()`

## 统一数据契约
provider 的 `find()` 继续返回 JSON 字符串，兼容现有 CLI 与 wrapper。

provider 的 `extract_summary_items(raw_payload)` 统一返回：

```json
{
  "items": [
    {
      "id": "string",
      "memory": "string",
      "score": 0.99,
      "created_at": "string",
      "updated_at": "string",
      "metadata": {}
    }
  ],
  "raw": {}
}
```

这样 agent 总结链路只关心 `items`，不关心 provider 原始形状。

## Provider 规则
### Mem0 Provider
- 保留现有 `mem0.min_score`
- 保留 `mem0.max_items`
- 只在 `mem0` provider 内部生效

### SQLite FTS Provider
- `FTS` 精确命中继续保留高置信度
- `embedding` 命中继续保留降权后的 score 和原始 `metadata.embedding_score`
- 总结前 item 提取直接信任 provider 已经过滤好的 `results`
- 不再额外套 `mem0.min_score`
- 只按 `remember_max_results` 与本地 embedding context 阈值控制候选

## Agent 路径调整
当前路径：
- `agent.py` 收到 JSON
- 直接调用 `extract_mem0_summary_payload()`
- 空结果时返回“没有找到匹配的记忆信息”

重构后路径：
- `agent.py` 收到 JSON
- 调用 `self.storage.remember_store().extract_summary_items(cleaned)`
- provider 返回统一 `items`
- agent 只负责把 `items` 渲染进 `remember summary` prompt

## 兼容策略
- `BaseRememberStore` 增加 `extract_summary_items()` 抽象方法
- CLI wrapper 和 CLI app 无需改输出协议
- `StorageService.remember_store()` 外部调用点保持不变
- 老测试中直接依赖 `extract_mem0_summary_payload()` 的地方，改为 provider 级测试

## 测试策略
- 新增 mem0 provider 提取测试
- 新增 sqlite provider 提取测试
- 新增 agent 集成测试：
  - 本地 SQLite 返回“拔智齿”记录时，最终回答不再错误判空
- 保留回归：
  - `uv run python -m unittest tests.test_core_behaviors.SQLiteRememberStoreTests tests.test_gui_events.StorageCliTests`
  - `uv run press-to-talk --text-input "usb测试版在哪" --no-tts`

## 风险与处理
- 风险：移动类定义会影响现有 import
  - 处理：先加兼容导出，再逐步迁移 import
- 风险：agent 假定所有 provider 返回 JSON 结构一致
  - 处理：由 provider 自己吸收差异，agent 只认统一 `items`
- 风险：测试覆盖不足导致回归
  - 处理：新增“检索到了但总结判空”的回归用例

## 推荐落地顺序
1. 定义 provider 基类和统一 `extract_summary_items()` 接口
2. 把 `Mem0RememberStore` 和 `extract_mem0_summary_payload()` 移入 `mem0` provider
3. 把 `SQLiteFTS5RememberStore` 移入 `sqlite_fts` provider，并实现本地 summary item 提取
4. 更新 `StorageService` 装配
5. 更新 `agent.py` 改走 provider 接口
6. 补单测与回归验证
