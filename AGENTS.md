# Voice Assistant

这个项目是一个本地语音助手，当前目录为独立项目根目录：
`/Users/weiwang/Projects/voice-assistant`

## 目标

- 启动后立刻进入录音链路
- 录音结束后做 STT、意图识别、工具调用、LLM 回复和 TTS 播报
- 优先支持本地记事与查询，非记事问题走聊天与联网工具

## 硬约束

- 任何改动都不能破坏“启动后第一时间开始录音”的体验
- 不允许为了 UI、TTS、LLM、MCP、日志、依赖加载或其它后处理阻塞开始录音
- 录音前路径应尽量只保留最少初始化
- 终端里保留 `rich` 动态 UI；非 TTY 环境例如 Raycast 要自动降级成纯文本状态输出

## 当前架构

- CLI 入口：`press-to-talk`
- Python 包：`press_to_talk`
- 主逻辑文件：`press_to_talk/core.py`
- 工作流配置：`workflow_config.json`
- 默认工作流兜底：`default_workflow.json`
- 意图抽取配置：`intent_extractor_config.json`
- 提示音资源：`assets/chimes/start.wav` 和 `assets/chimes/end.wav`

## 当前行为

- 录音开始音和结束音使用系统 `afplay`
- TTS 使用外部命令：
  `qwen-tts --play <text> --speaker serena --stream`
- Chat 分支会加载 `brave-search` 和 `fetch` MCP 工具
- Remember 分支使用外部脚本：
  `OPENCLAW_REMEMBER_SCRIPT`

## Remember 集成

- `remember` 的代码来源是外部兄弟仓库：
  `/Users/weiwang/Projects/ursoft-skills/skills/remember`
- 默认 remember 脚本路径指向：
  `/Users/weiwang/Projects/ursoft-skills/skills/remember/scripts/manage_items.py`
- 优先使用环境变量 `URSOFT_REMEMBER_SCRIPT` 覆盖脚本路径
- 为兼容旧配置，也接受 `OPENCLAW_REMEMBER_SCRIPT`，但新配置统一用 `URSOFT_REMEMBER_SCRIPT`
- 不要把 `remember` 目录拷贝进本仓库作为主方案，优先保持外部 repo 单一来源
- Remember 数据存储在 NocoDB `Items` 表
- 当前相关字段包括：
  `Name`、`Content`、`Type`、`Note`、`Photo`、`OriginalText`
- 搜索会覆盖：
  `Name`、`Content`、`Type`、`Note`、`OriginalText`
- 新增记录时会额外保存用户原始 STT 文本到 `OriginalText`

## 已知实现细节

- `remember` 查询结果会再次喂给 LLM 总结
- 第二次总结时必须同时传入：
  用户原始问题、结构化字段、工具原始输出
- 记事总结提示词已外置到 `workflow_config.json` 的 `remember_summary.system_prompt`
- Chat 系统提示词会在运行时注入当前时间和位置南京

## Git 工作流

- 每次开始改动前先检查工作区是否干净
- 如果工作区不干净，先确认现有改动，再继续处理当前任务
- 每次完成一轮改动后必须提交一次
- 任务结束前再确认一次提交已完成，确保改动已经落盘
- 如果当前环境不是 git 仓库，就先说明这一点，再按最近可用的版本控制流程处理

## 运行方式

- 开发运行：
  `uv run press-to-talk`
- 文本链路测试：
  `uv run press-to-talk --text-input "测试内容" --no-tts`
- 只测意图：
  `uv run press-to-talk --text-input "测试内容" --classify-only`
- 真实 mem0 e2e 只允许手动运行，不要在日常测试、默认回归或自动化检查里触发：
  `PTT_RUN_E2E=1 uv run python tests/mem0_e2e.py`

## 外部入口

- Raycast 脚本：
  `/Users/weiwang/Library/Mobile Documents/com~apple~CloudDocs/scripts/ask-xiaobaozang.sh`
  `/Users/weiwang/Library/Mobile Documents/com~apple~CloudDocs/scripts/press-to-talk.sh`

如果后续继续改这个项目，先检查：
1. 录音启动时延是否仍然足够低
2. Raycast 非 TTY 输出是否仍然可见
3. Remember 的字段映射和 NocoDB 表结构是否仍然一致
