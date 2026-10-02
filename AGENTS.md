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

- fast-path（`fast_chat.py`）先一次 TypeSafe（Jev System One）`POST /v1/systemone` 并行问两个 choice 问题（`ask_intent_and_delivery`）：`intent` 二分 `record` / `other`；`delivery` 二分 `paste` / `speak`（期望产出是"粘贴到光标处的内容"还是"播报回答"，配置在 `typesafe.delivery_question`），输出 `debug_info.typesafe_s`。
- `record` → 直写 Memos；`paste` → Harness（chat-fast）按 `harness_compose` 提示词（占位符 `%%INSTRUCTION%%`/`%%SELECTION%%`）产出最终内容，响应带 `action: "paste"`；`other`（询问/闲聊）→ Harness（chat-fast preset）拆词 → 一次 Memos CEL 查询（`memos.query_timeout_seconds` 默认 1.5s 短超时，异常/空 = 无上下文）→ Harness（chat-fast）带上下文回答，无匹配则正常闲聊（可 web_search）。
- `/v1/chat` 请求可选 `selected_text`（GUI 在窗口激活前从上一个前台应用捕获的选中文本）；响应新增 `action` 字段：`speak`（默认，播报）或 `paste`（reply 即应回贴的最终内容，GUI 用 Cmd+V 粘贴而非朗读）。慢路径（TypeSafe 不可用）退化为把选中文本拼进问句播报回答，不粘贴。
- 提示词在 `workflow_config.json`：`typesafe` 段（`intent_question` / `delivery_question`）与 `prompts` 段（`harness_keyword_extract` / `harness_answer` / `harness_compose`，占位符用 `%%QUERY%%`/`%%MEMOS%%`/`%%INSTRUCTION%%`/`%%SELECTION%%`，避免被 `${ENV}` 展开吞掉）；TypeSafe API key 在 `.env` 的 `TYPESAFE_API_KEY`（gitignore，远程 docker 机器需手动补写）。
- 未配置 key、网络失败或意图无法二分（返回 None）时静默降级：fast-path 返回 None，memo_web / main.py 回退 Harness Agent 兜底。

## Mac GUI 选中文本改写/生成（SelectionBridge）

- `mac_gui` 启动瞬间（窗口激活前）记录上一个前台应用（优先 `menuBarOwningApplication`，兼容 Raycast 等 accessory 启动器），用 AX `kAXSelectedText` 同步读选中文本；失败且已授权时走剪贴板兜底（激活目标应用 → 模拟 Cmd+C → 读剪贴板 → 还原剪贴板与焦点），见 `SelectionBridge.swift`。选中文本上限 16000 字符，超长截断。
- 需要「辅助功能」权限（AX 读取与 Cmd+C/Cmd+V 模拟按键共用授权）：系统设置 → 隐私与安全性 → 辅助功能，添加 `VoiceAssistantGUI` 二进制。未授权时功能降级为普通对话并在空闲页提示。
- 后端返回 `action=paste` 时，GUI 把 reply 写入剪贴板 → `yieldActivation` + 激活目标应用 → 模拟 Cmd+V，不播 TTS，结果卡片区显示"已粘贴到 …"，12 秒无交互自动退出（任何交互取消）；无法回贴时降级为"已复制到剪贴板"。焦点交换期间（捕获兜底/回贴）`applicationDidResignActive` 不触发退出（`isFocusExchangeInFlight` 保护）。

## Memos 查询

- 询问链路 Memos 查询（`_search_memos_cel`）只做**单次** CEL：`content.contains('词1') || ...`，`memos.query_timeout_seconds` 默认 1.5s 短超时；失败/空结果一律视为"无上下文"（返回 `[]`）继续闲聊，不做 fallback 翻页/重试/不可用异常。Memos API 正常仅 0.15s，不会拖垮 ≤8s 目标。
