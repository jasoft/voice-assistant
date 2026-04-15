# Storage Boundaries Design

## 背景
当前存储实现把以下职责混在一起：配置加载、SQLite CRUD、FTS 检索、CLI 参数解析、CLI 输出协议、子进程 wrapper、StorageService 装配、以及部分 LLM 查询改写接入。结果是：
- 单文件过大，修改容易引入回归。
- CLI 与库内实现边界不清，stdout/stderr 协议容易被破坏。
- history 和 memory 的 CRUD 虽然简单，但读写路径分散，测试难以聚焦。

## 目标
- 把 storage 重构为清晰分层：配置/模型、SQLite repository、CLI app、CLI wrapper、facade。
- 保持现有外部接口稳定：`StorageService`、`press_to_talk.storage_cli`、agent/GUI 调用方式不变。
- memory/history 都以简单 SQLite CRUD 为核心；LLM 仅负责用户口述转写后的查询改写与命令调用，不进入 repository 逻辑。

## 方案对比
### 方案 A：只整理 `service.py` 内部函数
优点：改动小。
缺点：边界仍然模糊，后续继续膨胀。

### 方案 B：按职责拆模块，保留对外接口不变（采用）
优点：低风险、可渐进迁移、测试容易分层。
缺点：需要移动代码并补兼容导出。

### 方案 C：重写成全新 ORM/命令总线
优点：结构最“新”。
缺点：过度设计，风险高，不符合当前项目规模。

## 目标结构
- `press_to_talk/storage/config.py`：`StorageConfig`、环境变量/工作流配置加载。
- `press_to_talk/storage/models.py`：`RememberItemRecord`、`SessionHistoryRecord`、协议接口。
- `press_to_talk/storage/sqlite_history.py`：history 表 CRUD。
- `press_to_talk/storage/sqlite_memory.py`：memory 表 CRUD、FTS 查询、查询过滤。
- `press_to_talk/storage/text_rewrite.py`：LLM query rewrite / memory translate。
- `press_to_talk/storage/cli_app.py`：storage CLI 的命令调度和输出协议。
- `press_to_talk/storage/cli_wrapper.py`：主程序调用 CLI 的子进程 wrapper。
- `press_to_talk/storage/service.py`：薄 facade，只做装配和兼容导出。

## 数据流
### Memory 查询
agent 提供原始 query → `keyword_rewriter` 生成检索词 → CLI 或本地 store 执行 SQLite/FTS 查询 → repository 返回结构化结果 → CLI 决定 stdout/stderr 呈现。

### History 查询
调用方传 query/limit → history repository 直接执行 SQLite 查询 → 返回记录列表。

## 错误处理
- repository 只抛 Python 异常，不处理终端输出。
- CLI app 统一负责异常转 JSON 错误并写到 stderr。
- CLI wrapper 统一负责把非零退出码转换成 `RuntimeError`。

## 测试策略
- repository 单测覆盖 memory/history CRUD 与查询过滤。
- CLI 单测覆盖 memory search 的 stdout/stderr 协议。
- wrapper 单测覆盖 JSON 解析和错误透传。
- 文本验收继续使用：`uv run press-to-talk --text-input "usb测试版在哪" --no-tts`。

## 非目标
- 不引入 ORM。
- 不修改启动录音链路。
- 不改变 GUI 或 agent 的公共调用接口。
