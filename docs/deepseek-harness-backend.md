# DeepSeek Harness 查询后端

语音助手的 `/v1/query` 可以把完整的自然语言请求转交给 DeepSeek Harness。语音助手只负责接收文本（以及可选图片）并等待回复；意图判断、工具调用、Mem0 读写和后续能力都由 Harness 的 Agent preset 负责。

## 配置

在运行语音助手的环境中设置：

```dotenv
PTT_QUERY_BACKEND=deepseek-harness
PTT_HARNESS_API_URL=http://127.0.0.1:3080
PTT_HARNESS_AGENT_PRESET=memo-mem0
```

Harness 源码已随本项目保存在 `deepseek-harness/`。安装依赖和构建产物属于本地运行状态，不提交到 git：

```bash
cd deepseek-harness
pnpm install --frozen-lockfile
pnpm run build
pnpm dsh web --port 3080
```

Harness 的模型、MCP 凭据和 preset 仍从运行机器的 `DSH_HOME` 读取；Mem0 凭据不写入本项目。

项目内的记忆 preset 策略源文件是
`config/deepseek-harness/agent-presets/memo-mem0/agent.cordis.yml`。它要求当前用户明确说出“记住/记录/保存”等意图才允许写入；普通陈述、关键词和查询只能读取。查询会在完整问题、实体组合和同义短语之间做有限的多轮召回。

要让另一台机器使用项目策略，将该 preset 目录同步到 `${DSH_HOME:-~/.dsh}/.agent-presets/memo-mem0`，并通过环境变量提供 `MEM0_MCP_TOKEN`（或兼容已有的 `MEM0_API_KEY`）。不要把 token 写入 git。

Harness Web API 使用 `POST /api/session.create`、`POST /api/session.prompt` 和 `POST /api/session.history`。语音助手会为每个认证用户保持一个 Harness 会话，并串行等待本轮最终助手消息。

## Mem0 用户隔离

`soj` 不放在语音助手的存储层里，而应放在 Harness 的 `memo-mem0` preset 指令及 Mem0 MCP 调用参数中。这样将来增加日历、邮件或其他工具时，所有能力仍由同一个 Harness Agent 统一编排。

当前机器使用的 `~/.dsh/.agent-presets/memo-mem0` 已同步到上述策略；新会话才会加载更新后的 preset，已经打开的 Harness 会话仍沿用创建时的旧策略。

## 召回问题的判定

召回失败要区分两类：如果 Mem0 中本来没有该条记录，多轮搜索也不会凭空找回；如果记录存在但一次语义搜索没有返回，才是查询策略问题。当前对本地 Markdown 的对照已经确认：护照和 Google 存放位置存在于 Mem0 并能稳定找回，而“老年交通卡放在手提包里”只存在于本地 Markdown 导出，当前 `user_id=soj` 的 Mem0 返回为 0 条。因此交通卡问题首先是数据未同步，不是阈值继续调低就能解决。

## 失败行为

Harness 不可达、会话创建失败、Agent 返回错误或等待超时，会由 `/v1/query` 返回 HTTP 502；不会静默退回本地 PocketBase 或直接调用 Mem0，避免同一次请求走两套记忆系统。
