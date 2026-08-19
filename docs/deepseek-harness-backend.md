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

Harness Web API 使用 `POST /api/session.create`、`POST /api/session.prompt` 和 `POST /api/session.history`。语音助手会为每个认证用户保持一个 Harness 会话，并串行等待本轮最终助手消息。

## Mem0 用户隔离

`soj` 不放在语音助手的存储层里，而应放在 Harness 的 `memo-mem0` preset 指令及 Mem0 MCP 调用参数中。这样将来增加日历、邮件或其他工具时，所有能力仍由同一个 Harness Agent 统一编排。

## 失败行为

Harness 不可达、会话创建失败、Agent 返回错误或等待超时，会由 `/v1/query` 返回 HTTP 502；不会静默退回本地 PocketBase 或直接调用 Mem0，避免同一次请求走两套记忆系统。
