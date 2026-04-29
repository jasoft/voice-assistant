# Voice Assistant

这个项目是一个本地语音助手，当前目录为独立项目根目录：
`/Users/weiwang/Projects/voice-assistant`

## 目标

- 启动后立刻进入录音链路
- 录音结束后做 STT、意图识别、工具调用、LLM 回复和 TTS 播报

## 硬约束

- 所有的提示词必须放到外部的 JSON 里面，严禁硬编码在 Python 或其它源代码中
- 任何改动都不能破坏“启动后第一时间开始录音”的体验
- 不允许为了 UI、TTS、LLM、MCP、日志、依赖加载或其它后处理阻塞开始录音
- 录音前路径应尽量只保留最少初始化
- 终端里保留 `rich` 动态 UI；非 TTY 环境例如 Raycast 要自动降级成纯文本状态输出
- 修改 Mac GUI 代码后，必须使用 `cd mac_gui && swift build -c release` 进行编译，以确保 `run-gui.sh` 脚本能秒开运行
- 执行层必须遵循行为树（Behavior Tree）架构，严禁引入复杂的嵌套 `if-else` 链路
- 所有日志输出必须重定向到 `stderr`，确保 `stdout` 仅用于干净的数据流（如 JSON）和事件传递

## 当前架构

- CLI 入口：`press-to-talk`
- Python 包：`press_to_talk`
- 执行层：`press_to_talk/execution/bt/` (基于行为树架构)
- 存储层隔离：`ptt-storage` (通过 subprocess 调用)
- 主逻辑文件：`press_to_talk/core.py`
- 工作流配置：`workflow_config.json`
- 意图抽取配置：`intent_extractor_config.json`
- 提示音资源：`assets/chimes/start.wav` 和 `assets/chimes/end.wav`

## 当前行为

- 录音开始音和结束音使用系统 `afplay`
- TTS 使用外部命令：
  `qwen-tts <text>`
- Chat 分支会加载 `brave-search` 和 `fetch` MCP 工具
- Remember 分支使用存储层 CLI：
  `ptt-storage memory search` / `ptt-storage memory add`
- 存储后端：支持 `Mem0` (云端) 和 `SQLite FTS5` (本地) 双引擎分发

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

## 收尾校验

- 必须通过所有测试集

## 文本处理约束

- 所有面向用户输入的文字归一、纠错、拆词和检索词生成，都要交给大模型通过提示词完成
- 不要在本地用字符串前缀匹配、关键词裁剪、手写正则去猜用户本意
- 代码里可以做结构化 JSON 解析，但不要用本地规则替代大模型的文字理解
