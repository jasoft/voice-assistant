# 架构设计 (ARCHITECTURE.md)

## 总体设计原则
1. **进程隔离**: 核心业务逻辑与数据存储层物理隔离，通过 CLI 子进程通信。
2. **行为驱动**: 执行层采用行为树 (Behavior Tree) 架构，替代复杂的 `if-else` 嵌套。
3. **插件化执行**: 支持多种执行模式 (Hermes, Memory-Chat, Standard Record/Find)。

## 核心组件关系
- **CLI/GUI (Trigger)**: 用户触发录音或输入文本。
- **Core Orchestrator**: 初始化环境，启动行为树。
- **Behavior Tree**: 
    - `Condition Nodes`: 判定意图、模式及上下文状态。
    - `Action Nodes`: 执行录音、意图提取、工具调用、LLM 总结、TTS 播报。
- **Blackboard**: 行为树内共享的“黑板”上下文，存储从录音到回复的全链路数据。
- **Storage Layer (Standalone)**: 提供独立的历史记录和长期记忆管理。

## 数据流向
1. `User Audio` -> `STT (OpenAI)` -> `Transcript`
2. `Transcript` -> `Intent Extractor (LLM)` -> `Structured Intent`
3. `Intent` -> `Behavior Tree Branching` -> `Tool Execution (e.g. Memory Search)`
4. `Result` -> `LLM Summarizer` -> `Natural Language Reply`
5. `Reply` -> `TTS (Qwen)` -> `Audio Output`
