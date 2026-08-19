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
../scripts/start_dsh.sh
```

`scripts/start_dsh.sh` 会从 `${DSH_HOME:-~/.dsh}/.credentials.yaml` 读取 `BRAVE_API_KEY`，只注入当前 DSH 进程，不会把密钥写入项目或输出到终端。也可以通过 `BRAVE_API_KEY` 环境变量覆盖，或用参数传给 DSH，例如 `../scripts/start_dsh.sh --port 3080`；局域网模式使用 `../scripts/start_dsh.sh --host 0.0.0.0 --port 3080`。

macOS 上不要把 Node 版 dsh 直接作为 launchd 子进程运行：该上下文可能让 Node 访问局域网模型网关时得到 `EHOSTUNREACH`，而普通用户会话访问正常。需要持久运行时使用用户会话里的 tmux：

```bash
./scripts/start_dsh_tmux.sh
```

该脚本默认使用 `voice-assistant-dsh` 会话、`0.0.0.0:3080`，重复执行不会创建重复实例；可用 `tmux attach -t voice-assistant-dsh` 查看 dsh 输出。

Harness 的模型、MCP 凭据和 preset 仍从运行机器的 `DSH_HOME` 读取；Mem0 凭据不写入本项目。

如果启用了 Brave Web Search，Brave provider 应安装在 `web` profile 中；`memo-mem0` preset 另外挂载模型侧的 `@deepseek-ai/dsh-tool-web`，这样 Agent 才能看到 `web_search` 工具。Web 服务和 Brave provider 不要重复写入记忆 preset，否则会触发 `service "web" has been registered`。

项目内的记忆 preset 策略源文件是
`config/deepseek-harness/agent-presets/memo-mem0/agent.cordis.yml`。它要求当前用户明确说出“记住/记录/保存”等意图才允许写入；普通陈述、关键词和查询只能读取。查询会在完整问题、实体组合和同义短语之间做有限的多轮召回。

要让另一台机器使用项目策略，将该 preset 目录同步到 `${DSH_HOME:-~/.dsh}/.agent-presets/memo-mem0`，并通过环境变量提供 `MEM0_MCP_TOKEN`（或兼容已有的 `MEM0_API_KEY`）。不要把 token 写入 git。

Harness Web API 使用 `POST /api/session.create`、`POST /api/session.prompt` 和 `POST /api/session.history`。语音助手会为每个认证用户保持一个 Harness 会话，并串行等待本轮最终助手消息。

## Memo 手机 Web 外壳

如果手机浏览器不能直接运行 Harness 网页，可以启动同源的移动端外壳：

```bash
./scripts/start_memo_web.sh --host 0.0.0.0 --port 10032
```

Docker Compose 会同时启动 `deepseek-harness`（内部端口 3080）和 `memo-web`（局域网端口 10032）。生产运行时，`config/deepseek-harness/` 由服务器单独保存并只读挂载到 dsh 的 `/root/.dsh`，不要把凭据或该目录提交到 git。

打开 `http://<这台电脑的局域网 IP>:10032/`，输入一条指令后，外壳会由服务器调用 `memo-mem0` Agent，等待 `session.history` 返回最终助手消息，再把纯文本结果显示在页面上。页面不直接加载 Harness 前端，也不依赖浏览器的 `crypto.randomUUID()`。

## Mem0 用户隔离

`soj` 不放在语音助手的存储层里，而应放在 Harness 的 `memo-mem0` preset 指令及 Mem0 MCP 调用参数中。这样将来增加日历、邮件或其他工具时，所有能力仍由同一个 Harness Agent 统一编排。

当前机器使用的 `~/.dsh/.agent-presets/memo-mem0` 已同步到上述策略；新会话才会加载更新后的 preset，已经打开的 Harness 会话仍沿用创建时的旧策略。

## 召回问题的判定

召回失败要区分两类：如果 Mem0 中本来没有该条记录，多轮搜索也不会凭空找回；如果记录存在但一次语义搜索没有返回，才是查询策略问题。当前对本地 Markdown 的对照已经确认：护照和 Google 存放位置存在于 Mem0 并能稳定找回，而“老年交通卡放在手提包里”只存在于本地 Markdown 导出，当前 `user_id=soj` 的 Mem0 返回为 0 条。因此交通卡问题首先是数据未同步，不是阈值继续调低就能解决。

## 失败行为

Harness 不可达、会话创建失败、Agent 返回错误或等待超时，会由 `/v1/query` 返回 HTTP 502；不会静默退回本地 PocketBase 或直接调用 Mem0，避免同一次请求走两套记忆系统。
