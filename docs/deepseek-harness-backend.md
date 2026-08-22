# DeepSeek Harness 查询后端

语音助手的 `/v1/query` 可以把完整的自然语言请求转交给 DeepSeek Harness。语音助手只负责接收文本（以及可选图片）并等待回复；意图判断、工具调用、Mem0 读写和后续能力都由 Harness 的 Agent preset 负责。

## 配置

在运行语音助手的环境中设置：

```dotenv
PTT_QUERY_BACKEND=deepseek-harness
PTT_HARNESS_API_URL=http://127.0.0.1:3080
PTT_HARNESS_AGENT_PRESET=memo-minimal
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

默认的 `memo-minimal` preset 不挂载 MCP 和 Web 工具；它只保留 bash，并通过 `/app/scripts/memo_api.py` 直接调用 Mem0 REST API，显著减少工具 schema 占用的上下文。旧的多轮 MCP preset 仍保留在 `memo-mem0`，便于回退。

项目内的记忆 preset 策略源文件是
`config/deepseek-harness/agent-presets/memo-minimal/agent.cordis.yml`。它要求当前用户明确说出“记住/记录/保存”等意图才允许写入；普通陈述、关键词和查询只能读取。

preset 还固定了唯一允许的三条 wrapper 命令（`add`、`search`、`list`）。这能避免模型猜出 `mem0 add ...` 这类不存在的命令，防止命令帮助文本进入会话并把后续请求撑爆 TPM 限额。

项目 preset 由 Compose 直接挂载到 `${DSH_HOME:-~/.dsh}/.agent-presets`。运行环境需要提供 `MEM0_MCP_TOKEN`（或兼容已有的 `MEM0_API_KEY`）。不要把 token 写入 git。

Compose 给一次性 Harness 容器设置 `DSH_PERMISSION_MODE=danger-full-access`。这是容器内执行 Mem0 REST wrapper 所需的部署选择；不要把同一配置照搬到本机开发或非隔离环境。

Harness 会主动清洗子进程里凭据形状的环境变量，所以 wrapper 在环境变量缺失时会从 `$DSH_HOME/.env` 读取 `MEM0_API_KEY` 或 `MEM0_MCP_TOKEN`。这样 token 仍留在机器本地凭据文件中，不会进入模型 shell 的进程环境。

Harness Web API 使用 `POST /api/session.create`、`POST /api/session.prompt` 和 `POST /api/session.history`。语音助手会为每个认证用户保持一个 Harness 会话，并串行等待本轮最终助手消息。

PTT API 默认只启动一个 uvicorn worker。异步任务表和 Harness 客户端会话都在进程内存里；多 worker 会让提交和轮询落到不同进程，表现为 `/v1/query/status/{job_id}` 间歇性 404。除非把这两类状态迁移到共享存储，否则不要调高 `--workers`。

## 持久化分工

- DeepSeek Harness 负责自然语言编排、工具调用和 Mem0 写入。
- PocketBase 是会话历史持久层。每次 `/v1/query` 成功返回后，语音助手会把用户问题和最终回复写入 `session_histories`，`/v1/history` 从这里读取最近 20 条。
- `/v1/memories` 在 Harness 模式下直接读取当前用户的全部 Mem0 记录，不再返回空的兼容数组。
- SQLite 不在当前查询链路中使用；旧 SQLite 文件只作为历史存档保留。
- 默认模型由运行机 `config/deepseek-harness/runtime/settings.yaml` 的 `agent-default-model.model` 控制，部署脚本当前用 `./scripts/set_dsh_model.sh free` 设置。如果以后切回 `fast`，脚本会把它的 `maxTokens` 固定到 1536（可用 `DSH_FAST_MAX_TOKENS` 覆盖），避免“输入 + 最大输出”超过 Groq 免费 tier 的 8000 TPM 预算。

## Memo 手机 Web 外壳

如果手机浏览器不能直接运行 Harness 网页，可以启动同源的移动端外壳：

```bash
./scripts/start_memo_web.sh --host 0.0.0.0 --port 10032
```

Docker Compose 会同时启动 `deepseek-harness`（内部端口 3080）和 `memo-web`（局域网端口 10032），两者都使用 `memo-minimal` preset。生产运行时，`config/deepseek-harness/` 由服务器单独保存并挂载到 dsh 的 `/root/.dsh`，供 Harness 保存 profile/session 运行状态；不要把凭据或该目录提交到 git。

打开 `http://<这台电脑的局域网 IP>:10032/`，输入一条指令后，外壳会由服务器调用当前配置的 Harness Agent，等待 `session.history` 返回最终助手消息，再把纯文本结果显示在页面上。页面不直接加载 Harness 前端，也不依赖浏览器的 `crypto.randomUUID()`。

## Mem0 用户隔离

`soj` 固定在极简 Agent 提示词和 `scripts/memo_api.py` 中，避免模型每轮选择错误作用域。

当前机器使用的 `~/.dsh/.agent-presets/memo-mem0` 已同步到上述策略；新会话才会加载更新后的 preset，已经打开的 Harness 会话仍沿用创建时的旧策略。

## 召回问题的判定

召回失败要区分两类：如果 Mem0 中本来没有该条记录，多轮搜索也不会凭空找回；如果记录存在但一次语义搜索没有返回，才是查询策略问题。当前对本地 Markdown 的对照已经确认：护照和 Google 存放位置存在于 Mem0 并能稳定找回，而“老年交通卡放在手提包里”只存在于本地 Markdown 导出，当前 `user_id=soj` 的 Mem0 返回为 0 条。因此交通卡问题首先是数据未同步，不是阈值继续调低就能解决。

## 失败行为

Harness 不可达、会话创建失败、Agent 返回错误或等待超时，会由 `/v1/query` 返回 HTTP 502；不会静默退回本地 PocketBase 或直接调用 Mem0，避免同一次请求走两套记忆系统。
