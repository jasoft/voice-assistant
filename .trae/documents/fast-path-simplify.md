# fast-path 记忆链路简化计划

## Context

大王认为当前 fast-path 记忆链路"框架设计得过于复杂"。现状 [fast_chat.py](file:///Users/weiwang/Projects/voice-assistant/press_to_talk/api/fast_chat.py) 存在多层冗余：

- **意图判断**：TypeSafe 三态（record/find/other）+ 正则降级，两套
- **关键词拆分**：TypeSafe Noul 优先 + LLM 兜底 + 正则兜底，三套
- **Memos 查询**：CEL + fallback 全量翻页 + 本地匹配 + 首包重试 + 不可用异常，多层容错
- **回答**：直连 LLM 总结 + 失败兜底展示原文

大王期望的简化链路（已确认）：

1. **一次 TypeSafe 调用**只判断"是否是记录"（二分）
2. **除了记录，其他都是询问**：Harness Agent 拆词 → 一次 CEL 搜索 → Harness 带上下文回答
3. **搜不到就继续闲聊**：fast-path 同时充当简单对话助手（天气、常识等），chat-fast preset 还支持 web 搜索
4. 拆词与回答都**改走 Harness Agent**（用户明确选择，不用现有 LLM 直连）

## 新链路

```
try_fast_memory_chat(query):
  1. TypeSafe 一次调用 ask_is_record(query) → "record" | "other" | None
     - None（TypeSafe 未配置/失败）→ return None，memo_web / main.py 回退 Harness 兜底
  2. record → 直写 Memos（现有 _record_memo，保持不动）→ 返回
  3. other（询问）:
     a. keywords = Harness(chat-fast) 拆词，输出 JSON 数组
     b. memos = CEL 一次查询（1.5s 超时，content.contains('k1') || ...），失败/空 = 无上下文
     c. reply = Harness(chat-fast) 回答（有 memos 则基于备忘回答；无则正常闲聊，可 web_search）
```

## 改动文件

### 1. [press_to_talk/utils/typesafe.py](file:///Users/weiwang/Projects/voice-assistant/press_to_talk/utils/typesafe.py)
- **删**：`gen_candidates()`（停用词切分/二次切分/候选池）、Noul 关键词逻辑、`noul_threshold`/`max_keywords` 解析
- **改**：`ask_intent_and_keywords()` → `ask_is_record(query)`：一次 `POST /v1/systemone` 只带一个 Choice（"这是一条要记录/保存的信息吗？"），返回 `"record" | "other" | None`
- **保**：`is_configured()`、`_cfg()`、HTTP 请求与错误处理、日志

### 2. [workflow_config.json](file:///Users/weiwang/Projects/voice-assistant/workflow_config.json)
- **typesafe 段**：`intent_question` 改为 is_record 二选一文案；**删** `keyword_question`、`candidate_stopwords`、`noul_threshold`、`max_keywords`
- **prompts 段**：新增两个模板（占位符风格与现有一致）：
  - `harness_keyword_extract`：指令"从问题提取 2-7 个检索关键词，只输出 JSON 数组"
  - `harness_answer`：指令"有相关备忘则基于备忘回答；没有则正常聊天回答（可 web 搜索）"
- `intents`/`find` 段保留（可能被其它模块引用，本次不动）

### 3. [press_to_talk/api/fast_chat.py](file:///Users/weiwang/Projects/voice-assistant/press_to_talk/api/fast_chat.py)
- **删**：`classify_memory_intent()` 及 `_FIND_PATTERNS`/`_RECORD_PATTERNS`、`_extract_keywords_with_llm()`、`_build_summary_messages()`、`_build_llm_client()`、`_llm_model()`、`stream_chat_completion_text()` 调用、`MemosQueryUnavailableError`、`_search_memos_cel` 的 fallback 翻页/重试/本地匹配
- **简**：CEL 查询缩为一次 `client.list_memos(page_size=10, filter_expr=..., timeout=1.5)`，异常/空直接返回 `[]`（无上下文）
- **增**：
  - `_extract_keywords_with_harness(query)`：新建一次性 `DeepSeekHarnessClient(agent_preset="chat-fast")`（复用 [harness/client.py](file:///Users/weiwang/Projects/voice-assistant/press_to_talk/harness/client.py) 的 `from_env`/`query`），指令用 `harness_keyword_extract` 模板，从 reply 解析 JSON 数组，失败返回空列表
  - `_answer_with_harness(query, memos)`：同样用 chat-fast，把用户问题 + 匹配备忘（或"无相关备忘"）拼进 `harness_answer` 模板，返回 reply
- **保**：`_record_memo()`、`_strip_tags_and_voice_prefix()`、`_build_memos_client()`、日志、`debug_info` 结构（`backend/intent/elapsed_s/typesafe_s/keywords/memo_count`）

### 4. [memo_web.py](file:///Users/weiwang/Projects/voice-assistant/press_to_talk/memo_web.py) / [main.py](file:///Users/weiwang/Projects/voice-assistant/press_to_talk/api/main.py)
- 调用方式不变（`try_fast_memory_chat`，`None` → Harness 回退）。仅保留 TypeSafe 失败兜底，逻辑更简单。

### 5. 测试
- `tests/test_typesafe_client.py`：改为测 `ask_is_record`（去掉关键词/候选相关断言）
- `tests/test_memos_search.py`：改为测单次 CEL（删 fallback/重试/不可用异常用例）
- `tests/test_memo_web_fastpath.py`：保留（mock `try_fast_memory_chat`），确认 record/询问走 fast-path、TypeSafe 失败回退 Harness
- 新增：`test_fast_chat_harness.py` —— mock `DeepSeekHarnessClient`，覆盖拆词 JSON 解析、CEL 调用（filter 含全部关键词）、无上下文时回答模板走闲聊、记录分支

### 6. [AGENTS.md](file:///Users/weiwang/Projects/voice-assistant/AGENTS.md)
- 更新"TypeSafe 意图与关键词"小节：意图二分 + Harness 拆词/回答 + 无结果闲聊的新链路描述

## 验证

1. `uv run pytest` 全量通过（预期减少一批测试，新增 harness mock 测试）
2. 本地链路：直接调用 `try_fast_memory_chat` 验证 record（"帮我记录：xxx"）、询问命中（"我的护照在哪里"）、无结果闲聊（"你好"），观察日志路径
3. 部署 `scripts/deploy.sh` 后远程验证 memo-web：记录/查询/闲聊三类请求都正常返回且 agent=fast-chat
4. 观察耗时：拆词 + CEL + 回答两次 Harness 调用，确认 ≤8s 目标；若超时由 chat-fast 快速模型兜底
