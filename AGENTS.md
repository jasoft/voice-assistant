# Voice Assistant

## 关键约束

- 启动后尽快开始录音；UI、TTS、LLM、MCP、日志和依赖加载不能阻塞录音前路径。
- 提示词放外部 JSON，不硬编码进源代码。输入归一、纠错、意图和检索词理解交给模型；结构化 JSON 可在本地解析，不用关键词/正则猜用户本意。
- 执行层保持行为树架构。日志写 `stderr`，`stdout` 留给数据与事件。
- 终端保留 rich 动态 UI，非 TTY（如 Raycast）降级为纯文本。
- Mac GUI 代码变更运行 `cd mac_gui && swift build -c release`，匹配 `run-gui.sh` 使用的发布构建。
- **服务端修改与凭证更新必须同步部署远程 Docker**：Apple Watch（真机与模拟器）默认直连远程 Docker 服务端（`https://va.soj.myds.me:1443`）。任何涉及服务端代码、API 端点（如 `/v1/chat`、`/v1/tts`）或环境变量的修改，在合并到 `main` 后**必须执行 `./scripts/deploy.sh` 同步并重启远程 Docker 服务**；若引入新凭据（如 `.env` 中的 API Key），必须先 SSH 到远程机器补齐 `~/voice-assistant/.env`，否则客户端仍将请求旧服务或因缺凭据降级。

## 入口与验证

- CLI 入口和参数以 `pyproject.toml` 与当前 CLI 帮助为准。`press_to_talk/` 是 Python 包，配置在外部 JSON。
- Python 测试用 pytest，选择受影响的用例；涉及录音或后端行为时再做对应链路验证。
- CLI 操作、Docker 部署和场景压测分别查 `.agents/skills/` 下的 `ptt-voice`、`deploy-to-docker`、`vibe-report`。
- 服务端部署：运行 `./scripts/deploy.sh` 自动升级补丁版本并发布至远程 `docker.home`，触发容器重建与重启。
- 历史架构和旧命令见 `docs/agent-context-reference.md`；不把其中的旧后端快照当作当前运行状态。

## 语音合成（TTS）

- 服务端流式 TTS（`/v1/tts`）：使用阿里云百炼 DashScope `qwen-audio-3.1-tts-flash` 模型，请求端点 `/services/audio/tts/SpeechSynthesizer`，默认音色 `yuxiaoyun_v3.1`，格式直接请求 `pcm` 并输出 24kHz/16-bit/单声道 PCM；服务端自动剥离可能存在的头部以防爆音。不添加多余的降级或 fallback 逻辑。

## fast-path 简化链路（Cloudflare clef-flash / TypeSafe + Harness + Memos）

- fast-path（`fast_chat.py`）先一次决策模型调用（优先 Cloudflare Workers AI `clef-flash`，兼容 TypeSafe Jev System One）`POST` 并行问两个 choice 问题（`ask_intent_and_delivery`）：`intent` 三分 `record` / `query` / `chat`；`delivery` 二分 `paste` / `speak`（期望产出是"粘贴到光标处的内容"还是"播报回答"，配置在 `typesafe.delivery_question`），输出 `debug_info.typesafe_s`。
- `record` → 一次 ChatCompletion 按 `prompts.memo_record_summary` 基于完整原话和可选选中文本整理正文，再写 Memos 并保留语音原文（禁止关键词删词；总结失败、空输出或截断时不写入）；`paste` → Harness（chat-fast）按 `harness_compose` 提示词（占位符 `%%INSTRUCTION%%`/`%%SELECTION%%`）产出最终内容，响应带 `action: "paste"`；`query`（用户明确要求查询个人备忘/记忆）→ ChatCompletion（直连 fast 模型，Harness 兜底）拆词 → 一次 Memos CEL 查询（`memos.query_timeout_seconds` 默认 5.0s 超时，异常/空 = 无上下文）→ Harness/Direct LLM（chat-fast）带上下文回答；`chat`（闲聊、常识问答等未明确要求查备忘的请求）→ Harness/Direct LLM（chat-fast）直接回答，**不检索 Memos**（避免不相关记忆污染上下文）。
- `/v1/chat` 请求可选 `selected_text`（GUI 在窗口激活前从上一个前台应用捕获的选中文本）；响应新增 `action` 字段：`speak`（默认，播报）或 `paste`（reply 即应回贴的最终内容，GUI 用 Cmd+V 粘贴而非朗读）。慢路径（决策模型不可用）退化为把选中文本拼进问句播报回答，不粘贴。
- 提示词在 `workflow_config.json`：`typesafe` 段（`intent_question` / `delivery_question`）与 `prompts` 段（`harness_keyword_extract` / `harness_answer` / `harness_compose`，占位符用 `%%QUERY%%`/`%%MEMOS%%`/`%%INSTRUCTION%%`/`%%SELECTION%%`，避免被 `${ENV}` 展开吞掉）；凭据在 `.env` 的 `CLOUDFLARE_AUTH_TOKEN`、`CLOUDFLARE_ACCOUNT_ID`（或 `TYPESAFE_API_KEY`，gitignore，远程 docker 机器需手动补写）。
- 未配置 key、网络失败或意图无法二分（返回 None）时静默降级：fast-path 返回 None，memo_web / main.py 回退 Harness Agent 兜底。

## Mac GUI 选中文本改写/生成（SelectionBridge）

- `mac_gui` 启动瞬间（窗口激活前）记录上一个前台应用（优先 `menuBarOwningApplication`，兼容 Raycast 等 accessory 启动器），用 AX `kAXSelectedText` 同步读选中文本；失败且已授权时走剪贴板兜底（激活目标应用 → 模拟 Cmd+C → 读剪贴板 → 还原剪贴板与焦点），见 `SelectionBridge.swift`。选中文本上限 16000 字符，超长截断。
- 需要「辅助功能」权限（AX 读取与 Cmd+C/Cmd+V 模拟按键共用授权）：系统设置 → 隐私与安全性 → 辅助功能，添加 `VoiceAssistantGUI` 二进制。未授权时功能降级为普通对话并在空闲页提示。
- 后端返回 `action=paste` 时，GUI 把 reply 写入剪贴板 → `yieldActivation` + 激活目标应用 → 模拟 Cmd+V，不播 TTS，结果卡片区显示"已粘贴到 …"，12 秒无交互自动退出（任何交互取消）；无法回贴时降级为"已复制到剪贴板"。焦点交换期间（捕获兜底/回贴）`applicationDidResignActive` 不触发退出（`isFocusExchangeInFlight` 保护）。

## Memos 查询

- 询问链路 Memos 查询（`_search_memos_cel`）只做**单次** CEL：`content.contains('词1') || ...`，`memos.query_timeout_seconds` 默认 5.0s 超时；失败/空结果一律视为"无上下文"（返回 `[]`）继续闲聊，不做 fallback 翻页/重试/不可用异常。Memos API 正常仅 0.15s，不会拖垮 ≤8s 目标。
