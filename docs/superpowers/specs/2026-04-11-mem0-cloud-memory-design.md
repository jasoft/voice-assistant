# Mem0 Cloud Memory Design

**日期：** 2026-04-11

**目标：** 用托管版 `mem0` 取代当前 remember 的本地/NocoDB 记忆层，固定 `user_id="soj"`，把记忆保存和召回统一走 Mem0 API，同时保持“启动后立刻开始录音”的体验不变。

## 背景

当前项目已经把录音、STT、意图识别、记忆归纳、记忆检索、LLM 总结拆成了多段流程。真正的记忆落库和检索都收口在 `StorageService.remember_store()` 上，这给记忆后端替换提供了稳定接缝。

用户要求改成纯云端 `mem0` 方案：

- 不再使用本地 SQLite 作为记忆数据源
- 不再使用 NocoDB 作为记忆数据源
- 不安装本地向量库或本地 embedding 模型
- `mem0` API Key 已放入 `.env`
- 固定 `user_id="soj"`
- `mem0` 返回 JSON，本地需要先抽取重点信息，再交给 Groq 总结后回复用户

## 设计原则

1. 不阻塞录音起步。任何 `mem0` 初始化都只能发生在录音结束后进入代理工作流时，不能挪到音频采集前。
2. 尽量复用现有接口。保留 `remember_add` / `remember_find` 两个工具名，避免动主流程结构。
3. 记忆检索与自然语言回复分层。`mem0` 负责存储与召回，Groq 负责面向用户的中文整理表达。
4. 配置最小化。记忆层只需要 `MEM0_API_KEY` 和可选 `MEM0_USER_ID`；默认用户 id 为 `soj`。

## 目标架构

### 1. 新增 `mem0` 记忆后端

在 `press_to_talk/storage/service.py` 中新增 `Mem0RememberStore`，实现现有 `BaseRememberStore` 接口：

- `add(memory, original_text="")`
- `find(query)`

`StorageService.remember_store()` 在 `backend == "mem0"` 时返回这个 store。

### 2. 保存记忆

`remember_add` 仍先复用当前“记忆句归纳”逻辑，把用户原话压缩为一句适合长期检索的中文记忆句。

随后调用 Mem0 平台 SDK：

- `MemoryClient(api_key=...)`
- `client.add(messages, user_id="soj")`

消息体只保留必要内容，避免把多余上下文送入记忆层。默认格式：

```python
[
    {"role": "user", "content": memory}
]
```

如果有原始 STT 文本，则作为 metadata 一并传递；如果 SDK 不接受该字段，则降级为仅保存归纳后的记忆句。

### 3. 检索记忆

`remember_find` 调用：

```python
client.search(query, user_id="soj")
```

Mem0 返回的是 JSON。项目本地新增一层容错解析，优先抽取这些信息：

- 记忆 id
- 记忆正文
- 相似度/score
- metadata
- created_at / updated_at
- categories 或标签类字段

抽取后的结构化结果会和原始 JSON 一起传给现有 remember summary 流程，由 Groq 生成最终中文答复。

## 配置变更

新增或调整以下环境变量：

- `VOICE_ASSISTANT_DATA_BACKEND=mem0`
- `MEM0_API_KEY`
- `MEM0_USER_ID`，默认 `soj`

现有 Groq/OpenAI-compatible 配置继续沿用，不额外修改对话模型接入层。

## 失败处理

1. 缺少 `MEM0_API_KEY` 时，remember 工具立即返回清晰错误，不影响非 remember 分支。
2. Mem0 API 异常时，remember 工具返回原始错误摘要，并记录日志。
3. JSON 结构字段缺失时，解析器使用宽松提取策略，至少保留原始 JSON 文本，避免总结链路完全失效。

## 测试策略

新增测试覆盖：

- `mem0` 后端配置选择
- `Mem0RememberStore.add/find/list_recent`
- `MEM0_API_KEY` 缺失时报错
- 结构化解析能从不同 JSON 形态中提取关键字段
- 现有 remember 总结链路仍能接收“结构化字段 + 原始输出 + 用户问题”

## 影响面

主要改动文件：

- `pyproject.toml`
- `press_to_talk/storage/service.py`
- `press_to_talk/core.py`
- `README.md`
- `tests/test_core_behaviors.py`

## 非目标

- 不修改录音触发时序
- 不引入本地向量库
- 不引入本地 embedding 模型
- 不迁移旧 NocoDB 数据到 Mem0
- 不改动 GUI 历史记录存储策略
