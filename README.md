# Press-to-Talk (PTT) 语音助手系统指南

`press-to-talk` 是一个标准化的 `uv` Python 包，集成了语音录制、意图识别、长短期记忆管理及 HTTP API 服务。

---

## 🚀 快速开始

### 1. 环境准备
确保已安装 `uv`，并在根目录创建 `.env` 文件，配置以下核心变量：
```bash
OPENAI_API_KEY=你的_api_key
OPENAI_BASE_URL=你的_base_url
PTT_STT_URL=你的_stt_服务地址
PTT_STT_TOKEN=你的_stt_鉴权令牌
```

### 2. 初始化数据库与迁移
首次运行或升级后，请执行迁移脚本以支持多用户隔离和 Peewee ORM：
```bash
uv run python3 scripts/migrate_v2_peewee.py
```

---

## 💻 常用运行命令

系统提供了多个 CLI 工具，可以通过 `uv run <command>` 直接调用。

### 🎙 语音交互 (Voice CLI)
核心交互入口，支持实时录音或文本注入。
- **启动实时录音交互**：`uv run ptt-voice`
- **纯文本测试（跳过录音）**：`uv run ptt-voice --text-input "查询壮壮的记录" --no-tts`
- **查看帮助**：`uv run ptt-voice --help`

### 🌐 HTTP API 服务
提供基于 FastAPI 的 Swagger 接口，支持多用户隔离。
- **启动 API 服务**：`uv run ptt-api --port 8000`
- **访问文档**：启动后访问 `http://localhost:8000/docs`

### 🔑 令牌管理 (Token Manager)
管理 API 访问令牌及关联的用户 ID。
- **列出所有令牌**：`uv run ptt-token list`
- **创建新用户令牌**：`uv run ptt-token add <user_id> --desc "备注信息"`
- **删除令牌**：`uv run ptt-token delete <token_string>`

### 📦 存储管理 (Storage CLI)
直接操作底层的历史记录和记忆数据。
- **查看历史列表**：`uv run ptt-storage history list`
- **搜索记忆**：`uv run ptt-storage memory search "关键词"`

---

## 🛠 开发与测试工具

系统内置了 `poethepoet` 任务执行器，简化常用操作：

| 命令 | 对应功能 |
| :--- | :--- |
| `uv run poe voice` | 启动标准语音交互 |
| `uv run poe ptt` | 启动文本注入测试 (默认 "你好") |
| `uv run poe api` | 启动 HTTP API 服务 |
| `uv run poe token` | 运行令牌管理器 |
| `uv run poe storage` | 运行存储管理器 |
| `uv run poe test` | 运行全量单元测试 (`pytest`) |
| `uv run poe doctor` | 运行系统诊断，检查音频设备和依赖 |

---

## 🧠 核心架构说明

### 1. 执行模式 (`--execution-mode`)
- **`memory-chat` (默认)**：全能模式。先检索相关记忆，再结合上下文进行对话。
- **`database`**：纯工具模式。只执行确定的数据库增删改查，不进行发散聊天。
- **`hermes`**：强制调用外部 Hermes 聊天引擎。

### 2. 多用户隔离
系统通过 `Authorization: Bearer <token>` 头部识别用户。
- 每个 `user_id` 拥有独立的 **Session History**（会话历史）和 **Remember Entries**（记忆条目）。
- 数据库底层通过 Peewee ORM 在所有查询中强制注入 `user_id` 过滤条件，确保公网环境下数据的绝对安全。

### 3. 记忆后端
默认使用 `sqlite_fts5`，支持全文搜索和向量搜索（需配置 Embedding 服务）。
- 配置文件：`workflow_config.json`
- 数据库文件：`data/voice_assistant_store.sqlite3`

---

## ⚙️ 环境变量参考
详细变量列表见原 `README.md` 或 `.env.example`。
- `PTT_MODEL`：主逻辑模型。
- `PTT_SUMMERIZE_MODEL`：总结回复模型。
- `PTT_HISTORY_DB_PATH`：数据库路径。

---
<tts>大王，全新的 README 指南已写好，所有运行命令一目了然！</tts>
