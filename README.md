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

`press-to-talk` 里的 `remember` 流程会先把用户原话归纳成一条可长期保存和检索的“记忆句”，再落库。数据库主查询字段不再拆成 `name/content/type`，而是统一围绕 `Memory` 检索。

默认情况下，remember 会直接走托管版 `mem0` API。项目会固定使用 `user_id=soj`（可通过 `MEM0_USER_ID` 覆盖），保存记忆时直接把用户原始对话写入 `mem0`，查询时直接用用户原始问句检索，再把 `mem0` 返回里分数大于 `0.8` 的前三条结果交给当前 Groq/OpenAI-compatible LLM 总结成中文回复。

`remember` 的脚本源码默认来自外部兄弟仓库 `ursoft-skills`：实际默认路径是 `/Users/weiwang/Projects/ursoft-skills/skills/remember/scripts/manage_items.py`。项目优先读取 `URSOFT_REMEMBER_SCRIPT`，同时兼容旧变量 `OPENCLAW_REMEMBER_SCRIPT`。

示例：

```bash
uv run press-to-talk --text-input "帮我记住充电宝是黑色的" --no-tts
uv run press-to-talk --text-input "记一下我妈生日是6月3号" --no-tts
uv run press-to-talk --text-input "记录一下，我今天安装了显示器的增高板" --no-tts
```

## 环境变量

- `OPENAI_API_KEY`：兼容 OpenAI 协议的 API Key
- `OPENAI_BASE_URL`：兼容 OpenAI 协议的服务地址
- `BRAVE_API_KEY`：Brave Search API Key
- `PTT_STT_URL` / `PTT_STT_TOKEN`：STT 服务地址和鉴权
- `PTT_MODEL`：意图抽取与对话使用的模型名
- `PTT_LOG_DIR`：运行日志目录，默认写到项目根目录下的 `logs/`
- `MEM0_API_KEY`：托管版 mem0 API Key
- `MEM0_USER_ID`：mem0 用户 ID，默认 `soj`
- `URSOFT_REMEMBER_SCRIPT`：覆盖默认 remember 脚本路径
- `OPENCLAW_REMEMBER_SCRIPT`：旧变量名，仍兼容，但建议迁移到 `URSOFT_REMEMBER_SCRIPT`
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
- 运行日志：每次启动都会自动写一份会话日志到 `logs/`
- 数据后端：当前只保留 `mem0`

## Mem0 云记忆

默认 remember 就会走 `mem0`，在 `.env` 里至少放这些：

```bash
MEM0_API_KEY=你的_mem0_key
MEM0_USER_ID=soj

GROQ_API_KEY=你的_groq_key
GROQ_BASE_URL=https://api.groq.com/openai/v1
PTT_GROQ_MODEL=qwen/qwen3-32b
```

文本链路测试：

```bash
uv run press-to-talk --text-input "帮我记住护照在书房抽屉里" --no-tts
uv run press-to-talk --text-input "护照在哪" --no-tts
uv run press-to-talk --text-input "把我记过的内容列出来" --no-tts
```

`remember_find` 和 `remember_list` 拿到的原始响应会保留 `mem0` JSON，再提取记忆正文、时间、score、metadata 等重点字段，随后交给当前模型整理成适合播报的中文回答。

## 历史记录

当前不再使用 `nocoDB` 或 `SQLite` 作为历史存储后端。GUI 的 History 面板会保留兼容入口，但默认返回空列表。

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
