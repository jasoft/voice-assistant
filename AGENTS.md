# Voice Assistant

## 关键约束

- 启动后尽快开始录音；UI、TTS、LLM、MCP、日志和依赖加载不能阻塞录音前路径。
- 提示词放外部 JSON，不硬编码进源代码。输入归一、纠错、意图和检索词理解交给模型；结构化 JSON 可在本地解析，不用关键词/正则猜用户本意。
- 执行层保持行为树架构。日志写 `stderr`，`stdout` 留给数据与事件。
- 终端保留 rich 动态 UI，非 TTY（如 Raycast）降级为纯文本。
- Mac GUI 代码变更运行 `cd mac_gui && swift build -c release`，匹配 `run-gui.sh` 使用的发布构建。

## 入口与验证

- CLI 入口和参数以 `pyproject.toml` 与当前 CLI 帮助为准。`press_to_talk/` 是 Python 包，配置在外部 JSON。
- Python 测试用 pytest，选择受影响的用例；涉及录音或后端行为时再做对应链路验证。
- CLI 操作、Docker 部署和场景压测分别查 `.agents/skills/` 下的 `ptt-voice`、`deploy-to-docker`、`vibe-report`。
- 历史架构和旧命令见 `docs/agent-context-reference.md`；不把其中的旧后端快照当作当前运行状态。

## fast-path 简化链路（TypeSafe + Harness + Memos）

- fast-path（`fast_chat.py`）先一次 TypeSafe（Jev System One）`POST /v1/systemone` 二分意图：`ask_is_record(query)` 返回 `record` / `other` / `None`，输出 `debug_info.typesafe_s`。
- `record` → 直写 Memos；`other`（询问/闲聊）→ Harness（chat-fast preset）拆词 → 一次 Memos CEL 查询（`memos.query_timeout_seconds` 默认 1.5s 短超时，异常/空 = 无上下文）→ Harness（chat-fast）带上下文回答，无匹配则正常闲聊（可 web_search）。
- 提示词在 `workflow_config.json`：`typesafe` 段（`intent_question` 二选一）与 `prompts` 段（`harness_keyword_extract` / `harness_answer`，占位符用 `%%QUERY%%`/`%%MEMOS%%`，避免被 `${ENV}` 展开吞掉）；TypeSafe API key 在 `.env` 的 `TYPESAFE_API_KEY`（gitignore，远程 docker 机器需手动补写）。
- 未配置 key、网络失败或意图无法二分（返回 None）时静默降级：fast-path 返回 None，memo_web / main.py 回退 Harness Agent 兜底。

## Memos 查询

- 询问链路 Memos 查询（`_search_memos_cel`）只做**单次** CEL：`content.contains('词1') || ...`，`memos.query_timeout_seconds` 默认 1.5s 短超时；失败/空结果一律视为"无上下文"（返回 `[]`）继续闲聊，不做 fallback 翻页/重试/不可用异常。Memos API 正常仅 0.15s，不会拖垮 ≤8s 目标。
