# 语音助手系统架构分析 (System Architecture)

> **创建日期：** 2026-04-19
> **状态：** 已根据 2026-04-18 行为树重构计划完成梳理

## 1. 核心链路概述

本项目是一个基于“按键触发 (Press-to-Talk)”模式的智能语音助手。其核心流程遵循：**音频采集 -> 语音转文字 (STT) -> 意图识别 -> 行为树执行 -> 存储检索 (CLI 隔离) -> 结果反馈 (TTS/GUI)**。

## 2. 详细流程图 (Mermaid)

```mermaid
graph TD
    %% 输入层
    User((大王)) -- 按键松开 --> PTT[ptt_voice.py / GUI]
    PTT --> AudioRecord[录音采集: recorder.py]
    AudioRecord --> STT[语音转文字: stt.py]
    STT --> Transcript[文本 Transcript]

    %% 执行层 (行为树)
    Transcript --> BT_Engine[执行引擎: execution/bt]
    
    subgraph BehaviorTree [Master Execution Tree]
        direction TB
        Root{Root Selector} --> IntentSeq[Sequence: 意图分析]
        IntentSeq --> ExtractIntent[Action: 提取意图]
        
        Root --> Dispatcher{Selector: 模式分发}
        
        %% 分支 1: 记录模式
        Dispatcher --> RecordSeq[Sequence: 记录模式分支]
        RecordSeq --> IsRecord[Condition: 意图为 Record?]
        RecordSeq --> SaveMem[Action: 保存记忆]
        
        %% 分支 2: 检索总结模式
        Dispatcher --> SearchSeq[Sequence: 检索总结分支]
        SearchSeq --> SearchDB[Action: 执行检索]
        SearchDB --> HasHits[Condition: 有命中?]
        SearchSeq --> Summarize[Action: LLM 总结回复]
        
        %% 分支 3: 聊天兜底
        Dispatcher --> ChatSeq[Sequence: 聊天兜底]
        ChatSeq --> IsChat[Condition: 允许聊天?]
        ChatSeq --> FallbackLLM[Action: 通用 LLM 回复]
    end

    %% 将执行引擎连接到行为树的根节点
    BT_Engine --> Root
    
    %% 存储层 (进程隔离)
    SaveMem --> StorageService
    SearchDB --> StorageService
    StorageService[StorageService Facade] --> CLIWrapper[CLI Wrapper]
    CLIWrapper -- subprocess --> StorageCLI[storage_cli.py]
    
    subgraph StorageLayer [存储层边界]
        StorageCLI --> ProviderSwitch{Provider 切换}
        ProviderSwitch --> Mem0[Mem0 Cloud Provider]
        ProviderSwitch --> SQLite[SQLite FTS5 Provider]
        StorageCLI --> HistoryDB[(历史记录 SQLite)]
    end

    %% 输出层
    Summarize --> FinalReply
    SaveMem --> FinalReply
    FallbackLLM --> FinalReply
    FinalReply[最终回复文本] --> TTS[语音合成: tts.py]
    TTS --> Speaker((扬声器播报))
    FinalReply --> GUI_Display[GUI 界面显示]
```

## 3. 关键设计特性

### 3.1 行为树 (Behavior Tree) 执行引擎
- **位置**: `press_to_talk/execution/bt/`
- **优势**: 取代了传统的 `if-else` 嵌套逻辑。通过 `Blackboard` (黑板) 模式共享上下文。
- **节点类型**:
    - `Selector`: 只要有一个子节点成功就返回成功（用于分支切换）。
    - `Sequence`: 所有子节点必须全部成功（用于线性任务流）。
    - `Action/Condition`: 具体的逻辑单元（如 LLM 调用、数据库查询判断）。

### 3.2 存储层边界隔离 (Storage Decoupling)
- **实现方式**: 主程序不直接访问数据库，而是通过 `subprocess` 调用 `storage_cli.py`。
- **优势**: 
    - **纯净度**: 存储层不包含 LLM 逻辑，只负责数据增删改查。
    - **稳定性**: 数据库操作异常不会直接导致主程序崩溃。
    - **多后端支持**: 可以在 `Mem0` (云端) 和 `SQLite FTS5` (本地) 之间无缝切换。

### 3.3 多 Provider 记忆架构
- **核心类**: `BaseRememberStore`
- **支持**: 
    - `Mem0RememberStore`: 云端记忆，利用 Mem0 平台的向量检索能力。
    - `SQLiteFTS5RememberStore`: 本地记忆，结合 FTS5 全文检索与向量嵌入 (Embedding)。

## 4. 目录职责划分

- `/press_to_talk/execution`: 执行层，包含行为树节点与组装器。
- `/press_to_talk/storage`: 存储层，包含 Provider 实现与 CLI Wrapper。
- `/press_to_talk/audio`: 音频处理（录音、STT、TTS、提示音）。
- `/press_to_talk/agent`: 意图识别与 LLM 交互逻辑。
- `/mac_gui`: Swift 实现的 macOS 客户端界面。
- `/docs`: 设计规范与实施计划。

## 5. HTTP API 与多用户支持

系统提供基于 FastAPI 的 RESTful API (`/v1/query`, `/v1/history`, `/v1/memories`)，用于支持移动端或 Web 端接入。

### 5.1 多用户隔离
- **鉴权机制**: 通过 `Authorization: Bearer <API_KEY>` 进行身份校验。
- **数据隔离**: API 层通过 `get_user_id` 依赖从 Token 解析出 `user_id`，并将其透传至执行层与存储层。所有的数据库查询（历史、记忆）均带有 `user_id` 过滤条件。

### 5.2 日志记录与脱敏
- **Middleware**: 所有的 API 请求均经过 `LoggingMiddleware`。
- **脱敏审计**: 
    - 自动隐藏 `Authorization` Header（仅保留首尾字符）。
    - 截断过长的请求体 (Body)，防止日志膨胀。
    - 记录 Client IP、URL、Method 等元数据。

### 5.3 结构化照片附件
API 支持在 `/v1/query` 请求中携带结构化的 `photo` 节点：
```json
{
  "query": "这张发票报销了吗？",
  "photo": {
    "type": "base64",
    "data": "...",
    "mime": "image/png"
  }
}
```
系统会自动处理 Base64 解码或 URL 下载，并将文件存入 `data/photos/` 目录，生成的本地路径会注入黑板 (Blackboard) 供行为树节点使用。API 响应中会返回 `photo_url` 供前端展示。

### 5.4 静态资源访问与 URL 映射
为了让客户端能直接展示存储在服务器上的图片，系统通过 FastAPI 挂载了静态资源：
- **挂载点**: `/assets` -> 映射到本地目录 `data/photos/`
- **URL 转换**: 系统内存储的 `photo_path` (如 `photos/abc.jpg`) 会被自动转换为 Web 可访问的 `photo_url` (如 `/assets/abc.jpg`)。
- **涉及模型**: `QueryResponse` 和 `MemoryItem` 模型均包含 `photo_url` 字段。

