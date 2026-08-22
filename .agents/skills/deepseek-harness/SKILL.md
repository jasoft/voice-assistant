---
name: deepseek-harness
description: 调用 DeepSeek Harness Web API 或语音助手封装接口，完成个人记忆问答、明确记录、检索和 Agent 编排。
---

# DeepSeek Harness 接口使用技能

本技能教一个 AI 客户端安全、稳定地使用当前项目里的 DeepSeek Harness。核心原则是：**调用方只提交用户原文并等待最终助手文本；意图判断、工具调用、Mem0 读写和 Web 搜索由 `memo-mem0` Agent 负责。**

## 选择正确的入口

| 场景 | 使用接口 | 说明 |
| --- | --- | --- |
| 外部客户端接入个人助理 | `POST /v1/query` | 语音助手 API 封装，带 API Key 鉴权；推荐给第三方 AI/自动化 |
| 直接驱动 Harness 会话 | `POST /api/session.*` | Harness Web RPC，可控制会话创建、提问和读取历史 |
| 手机或局域网演示外壳 | `POST /api/query`（Memo Web） | 无多用户鉴权，只用于可信局域网 |

不要把 `/v1/memories` 当成 Mem0 浏览器。Harness 后拥有 Mem0，该端点为了兼容旧 GUI 固定返回空数组。要读写记忆，仍应发送自然语言请求。

## 环境变量

### 语音助手封装层

```dotenv
PTT_QUERY_BACKEND=deepseek-harness
PTT_HARNESS_API_URL=http://127.0.0.1:3080
PTT_HARNESS_AGENT_PRESET=memo-mem0
PTT_HARNESS_TIMEOUT_SECONDS=60
PTT_API_KEY=<caller-api-key>
```

- Docker Compose 内部地址是 `http://deepseek-harness:3080`。
- 本机默认地址是 `http://127.0.0.1:3080`。
- `PTT_HARNESS_API_URL` 可指向反向代理；如代理需要 Bearer Token，设置 `PTT_HARNESS_API_TOKEN`。
- `PTT_QUERY_BACKEND` 接受 `deepseek-harness` 或 `harness`。

### 认证与用户隔离

语音助手 API 要求：

```http
Authorization: Bearer <PTT_API_KEY>
```

Harness 后端模式下，服务端把通过校验的调用方映射到 `PTT_USER_ID`，默认为 `soj`。每个认证用户在语音助手进程内复用独立的 Harness 会话，请求会被串行化，避免同一会话并发交错。

## 推荐：调用 `/v1/query`

```bash
BASE=http://127.0.0.1:10031
API_KEY="$PTT_API_KEY"

curl -sS "$BASE/v1/query" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "我的护照放在哪里？",
    "mode": "memory-chat"
  }' | jq .
```

成功响应：

```json
{
  "reply": "护照在白柜子第二层。",
  "memories": [],
  "images": [],
  "query": "我的护照放在哪里？",
  "debug_info": {
    "backend": "deepseek-harness",
    "agent_preset": "memo-mem0",
    "session_id": "<harness-session-id>"
  }
}
```

`memories` 是旧 API 兼容字段；即使为空，也不代表 Agent 没有检索 Mem0。最终答案以 `reply` 为准。

带图片时：

```json
{
  "query": "记录这张照片里的收据信息。",
  "photo": {
    "type": "base64",
    "data": "<base64-or-data-uri>",
    "mime": "image/png"
  }
}
```

`type=url` 时服务端会先下载 URL 并转换成 Base64。支持 PNG、JPEG、WebP 和 GIF。图片只是随本轮问题传给 Agent；是否写入长期记忆仍由 `memo-mem0` preset 的“明确记录请求”规则决定。

读取最近已完成对话：

```bash
curl -sS "$BASE/v1/history" \
  -H "Authorization: Bearer $API_KEY" \
  -H 'Content-Type: application/json' | jq .
```

返回最多 20 条按时间倒序的 user/assistant 成对记录：

```json
[
  {
    "session_id": "<harness-session-id>:<event-seq>",
    "transcript": "我把钥匙放哪里了？",
    "reply": "钥匙在玄关托盘里。",
    "created_at": "2026-08-20T10:00:00+08:00"
  }
]
```

## 直连：Harness Web RPC 协议

所有方法都是 HTTP POST：

```text
POST {PTT_HARNESS_API_URL}/api/{method}
Content-Type: application/json
```

请求信封固定为：

```json
{
  "type": "client-request",
  "rpcId": "<unique-request-id>",
  "method": "<session.create|session.prompt|session.history>",
  "payload": {}
}
```

成功响应必须检查两层：

```json
{
  "type": "server-response",
  "rpcId": "<same-id>",
  "result": {
    "ok": true,
    "value": {}
  }
}
```

若 `result.ok` 不是 `true`，读取 `result.error.code` 与 `result.error.message` 并停止本轮，不要猜测回复。

### 一次完整提问的状态机

1. **创建或复用会话**

   ```json
   {
     "method": "session.create",
     "payload": { "agentPreset": "memo-mem0" }
   }
   ```

   从 `result.value.sessionId` 保存会话 ID。

2. **建立基线**

   ```json
   {
     "method": "session.history",
     "payload": {
       "sessionId": "<sessionId>",
       "maxMessages": 200
     }
   }
   ```

   遍历 `value.events[]`，取每个事件 `event.seq` 的最大值作为 `baselineSeq`。

3. **提交用户消息**

   ```json
   {
     "method": "session.prompt",
     "payload": {
       "sessionId": "<sessionId>",
       "mode": "queue",
       "content": [
         { "type": "text", "text": "<用户原话>" }
       ]
     }
   }
   ```

   图片追加为第二个 content block：

   ```json
   {
     "type": "image",
     "mediaType": "image/png",
     "data": "<pure-base64>"
   }
   ```

4. **轮询直到出现新的最终助手文本**

   继续调用 `session.history`，只接受满足全部条件的事件：

   - `event.seq > baselineSeq`
   - `event.type == "assistant/message"`
   - `event.data.message.role == "assistant"`
   - 把 `message.content[]` 中 `type == "text"` 的 `text` 连接后非空

   取最新一条符合条件的文本作为最终回复。空的 `assistant/message` 可能是工具调用或中间边界，必须继续等待。

5. **识别终态错误**

   如果新事件中存在：

   ```json
   {
     "type": "turn/end",
     "data": {
       "reason": {
         "kind": "error",
         "error": { "message": "..." }
       }
     }
   }
   ```

   立即失败并把 `reason.error.message` 返回给上层，不要等满超时。

6. **超时**

   默认总等待 60 秒，轮询间隔 0.25 秒。超时文案格式为“等待 DeepSeek Harness 回复超时”。收到 `session-not-found` 时丢弃缓存会话并重建，但当前业务请求仍应失败重试由调用方决定。

### 最小 curl 流程

```bash
export DSH_BASE="${DSH_BASE:-http://127.0.0.1:3080}"
rpc_id="dsh-$(date +%s)-$RANDOM"

curl -sS "$DSH_BASE/api/session.create" \
  -H 'Content-Type: application/json' \
  -d "{\"type\":\"client-request\",\"rpcId\":\"$rpc_id-create\",\"method\":\"session.create\",\"payload\":{\"agentPreset\":\"memo-mem0\"}}" \
  | tee /tmp/dsh-create.json | jq '.result'

SESSION_ID="$(jq -r '.result.value.sessionId' /tmp/dsh-create.json)"

curl -sS "$DSH_BASE/api/session.prompt" \
  -H 'Content-Type: application/json' \
  -d "{\"type\":\"client-request\",\"rpcId\":\"$rpc_id-prompt\",\"method\":\"session.prompt\",\"payload\":{\"sessionId\":\"$SESSION_ID\",\"mode\":\"queue\",\"content\":[{\"type\":\"text\",\"text\":\"我的护照放在哪里？\"}]}}" \
  | jq '.result'

curl -sS "$DSH_BASE/api/session.history" \
  -H 'Content-Type: application/json' \
  -d "{\"type\":\"client-request\",\"rpcId\":\"$rpc_id-history\",\"method\":\"session.history\",\"payload\":{\"sessionId\":\"$SESSION_ID\",\"maxMessages\":200}}" \
  | jq '[.result.value.events[] | {seq: .event.seq, type: .event.type, text: [.event.data.message.content[]? | select(.type == "text") | .text] | join("")}]'
```

生产代码不要依赖 shell 变量拼接复杂 JSON；优先用 Python/httpx 或其他 JSON 库，并为每个 RPC 生成全局唯一 `rpcId`。

## Python 客户端约定

项目内已有异步实现：

```python
from press_to_talk.harness import DeepSeekHarnessClient, HarnessError

client = DeepSeekHarnessClient.from_env()
try:
    result = await client.query("我的护照放在哪里？")
    print(result["reply"])
finally:
    await client.close()
```

`query()` 已经处理会话复用、基线计算、串行锁、Base64 图片、轮询、终态错误和响应映射。除非需要扩展协议，否则不要复制这套状态机。

## Agent 行为契约：`memo-mem0`

当前内置策略位于 `config/deepseek-harness/agent-presets/memo-mem0/agent.cordis.yml`。

- 所有 Mem0 操作的 `user_id` 固定为 `soj`。
- 只有当前消息明确包含“记住”“记一下”“保存”“存到记忆里”等记录请求才允许 `add_memory`。
- 陈述事实、关键词和普通提问只能触发查询，不能自动写入。
- `add_memory` 必须保存用户原文到顶层 `text`，不改写，并使用 `infer=false`。
- 查询先 `search_memories`，过滤器必须是：

  ```json
  {"AND":[{"user_id":"soj"}]}
  ```

- 第一次用完整问题检索；不足时最多再补两轮：一轮用实体组合，一轮用同义表达。
- 用户要求全部记录或近期事件时，用 `get_memories` 加 `user_id` 过滤获取集合后筛选。
- 修改、纠正、更新或删除必须先找到准确 `memory_id`，且保持用户隔离。
- 回答只依据工具返回内容；人名、地名、日期、数字、书名和位置保留原文，不编造。
- 若挂载了 Brave 工具，Agent 可执行 `web_search`；Web 服务与记忆 MCP 不能重复注册同一个 profile/service。

因此，其他 AI 构造 prompt 时应**传递用户原话**，不要替 Agent 预先把陈述改写成“请记住……”，也不要在客户端自行维护第二份记忆库。

## 错误处理

| 现象 | 判定 | 处理 |
| --- | --- | --- |
| PTT API 401 | `PTT_API_KEY` 不匹配或缺 Bearer Token | 检查客户端 Authorization，不要打印密钥 |
| PTT API / Memo Web 502 | Harness 不可达、preset 缺失、Agent 失败或等待超时 | 先查 Harness 进程和 `PTT_HARNESS_API_URL`，再查 preset 与凭据 |
| `agent-preset-not-found` | 目标 DSH_HOME 没有 preset | 同步 `memo-mem0` 目录到运行机器 `${DSH_HOME:-~/.dsh}/.agent-presets/` |
| 长时间无最终文本 | 中间工具调用正常但未结束 | 继续按 seq 轮询，不要把空 assistant 事件当答案 |
| 新会话没有新策略 | preset 在会话创建时固化 | 结束旧会话，让下一次 `session.create` 加载新策略 |
| `/v1/memories` 为空 | Harness 模式的兼容行为 | 通过 `/v1/query` 用自然语言查询记忆 |

## 安全边界

- 不要把 `MEM0_MCP_TOKEN`、Brave key、模型 key 或 `PTT_API_KEY` 写入仓库、日志、测试快照或示例输出。
- 不要绕过 Harness 直接访问 Mem0；这会造成双记忆系统。
- 不要把无鉴权的 Memo Web 暴露到公网。
- 生产配置目录 `config/deepseek-harness/runtime/` 对应服务器上的 DSH 运行状态，不应提交凭据。
- 敏感存放位置可以回答位置本身；不要主动泄露密码、验证码等内容。

## 上线前最小验收

1. 健康检查：Harness Web 可访问，Memo Web `GET /health` 返回 `{"status":"ok"}`。
2. 只读召回：问一个已知存在的记忆事实，确认 `reply` 引用真实内容。
3. 明确写入：说“记住……”后再次查询，确认能找回。
4. 写入门禁：只陈述事实而不说记录动词，随后确认没有新增记忆。
5. 历史：调用 `/v1/history`，确认最新 user/assistant 成对记录可见。
6. 失败路径：临时指向不可达端口，确认得到 502 且不静默回退 PocketBase。
