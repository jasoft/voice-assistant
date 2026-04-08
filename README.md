# press-to-talk

`press-to-talk` 已整理成标准 `uv` Python 包，可直接安装和运行，并通过外部 `qwen-tts` 播报回复。

运行前先在仓库根目录准备 `.env`。

## 运行方式

在 [`voice-assistant`](/Users/weiwang/Projects/voice-assistant) 目录下：

```bash
uv run press-to-talk --help
uv run press-to-talk
uv run python -m press_to_talk --help
uv run press-to-talk --text-input "帮我记住充电器是黑色的" --no-tts
uv run press-to-talk --text-input "帮我找下护照在哪" --classify-only --no-tts
uv run press-to-talk --intent-samples-file testdata/intent_samples.jsonl
```

默认会直接调用系统里可用的 `qwen-tts` 命令，并使用 `--play --speaker serena --stream` 播报回复。

## 记忆功能

`press-to-talk` 里的 `remember` 流程不再只记“位置”，而是统一支持：

- 位置
- 日期和生日
- 特征描述
- 事件提醒
- 备注补充

示例：

```bash
uv run press-to-talk --text-input "帮我记住充电宝是黑色的" --no-tts
uv run press-to-talk --text-input "记一下我妈生日是6月3号" --no-tts
uv run press-to-talk --text-input "帮我记一下明天上午十点开会" --no-tts
```

## 环境变量

- `OPENAI_API_KEY`：兼容 OpenAI 协议的 API Key
- `OPENAI_BASE_URL`：兼容 OpenAI 协议的服务地址
- `BRAVE_API_KEY`：Brave Search API Key
- `PTT_STT_URL` / `PTT_STT_TOKEN`：STT 服务地址和鉴权
- `URSOFT_REMEMBER_SCRIPT`：remember 工具脚本路径，默认指向 `~/Projects/ursoft-skills/skills/remember/scripts/manage_items.py`
- `PTT_MODEL`：意图抽取与对话使用的模型名
- `VOICE_ASSISTANT_HISTORY_NOCODB_TABLE_ID`：GUI 会话历史记录表 ID，默认指向 `Voice Assistant History`
- 其余录音阈值、TTS 参数、输出路径也都支持从 `.env` 覆盖

## 开发说明

- CLI 入口：`press-to-talk`
- Python 模块入口：`press_to_talk`
- 兼容旧脚本：`python ptt_voice.py`
- TTS 使用：`qwen-tts`
- 文字测试：`--text-input` 或 `--text-file` 可直接跳过录音和 STT
- 静默测试：`--no-tts` 可跳过语音播报，只验证意图和工具链路
- 分类测试：`--classify-only` 只输出意图标签
- 回归样本：`--intent-samples-file testdata/intent_samples.jsonl`
- 记忆语义：`record` 覆盖位置、日期、特征、事件、备注，不再局限于 location

## 回归测试

```bash
uv run press-to-talk --intent-samples-file testdata/intent_samples.jsonl
```

这个模式会逐条跑样本，输出每条预测的意图并给出总匹配数。适合在改提示词、改工具参数、改 Skill 文案后做快速回归。

## 依赖

- `numpy`
- `qwen-tts`
- `sounddevice`
- 系统命令：`curl`、`openclaw`
