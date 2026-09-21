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

## TypeSafe 意图与关键词

- fast-path（`fast_chat.py`）的意图判断与关键词拆分优先走 TypeSafe（Jev System One）：一次 `POST /v1/systemone` 并行完成意图 Choice + 逐候选词 Noul，约 0.6s，输出 `debug_info.typesafe_s`。
- 提示词、阈值、候选停用词在 `workflow_config.json` 的 `typesafe` 段（占位符用 `%%KEYWORD%%`，避免被 `${ENV}` 展开吞掉）；API key 在 `.env` 的 `TYPESAFE_API_KEY`（gitignore，远程 docker 机器需手动补写）。
- 未配置 key、网络失败或 intent=other 时静默降级：意图回正则、关键词回 LLM。

## Memos 查询

- find 链路 Memos 查询（`_search_memos_cel`）对服务偶发抖动免疫：CEL 与 fallback 均用短超时（`memos.query_timeout_seconds`，默认 1.5s），CEL 失败/空结果时全量翻页（`search_page_size` × `max_fallback_pages`）+ 本地子串匹配。Memos API 正常仅 0.15s，偶发超时不会拖垮 ≤8s 目标。
