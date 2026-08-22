---
name: deepseek-harness
description: 通过语音助手外部 API 完成个人记事、记忆问答、历史查看和记忆列表读取。
---

# 个人记事与查询 API

这是一个面向外部 AI 的个人助理接口。调用方只需要发送用户原话，服务端负责理解意图、检索记忆、保存记忆并生成回复。

## 基础信息

```text
BASE=http://docker.home:10031
```

所有请求都要带：

```http
Authorization: Bearer <PTT_API_KEY>
Content-Type: application/json
```

不要在日志、代码仓库、示例输出或回复里暴露 API key。

## 可用接口

### 1. 记事或提问

```bash
curl -sS "$BASE/v1/query" \
  -X POST \
  -H "Authorization: Bearer $PTT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query":"<用户原话>"}'
```

- 用户想保存时，保留明确的记录表达，例如“记住……”“记录一下……”。
- 用户想查找时，直接传完整问题，例如“我的护照放在哪里？”。
- 不要替用户改写成另一套指令，也不要在客户端维护第二份记忆库。

成功响应的重点是 `reply`：

```json
{
  "reply": "已记住：护照在白柜子第二层。",
  "query": "记住护照在白柜子第二层。",
  "memories": [],
  "images": [],
  "debug_info": {}
}
```

`memories` 和 `images` 是兼容字段，普通客户端应以 `reply` 为准。

如果调用方不能长时间等待，可提交后台任务：

```bash
curl -sS -X POST "$BASE/v1/query/async" \
  -H "Authorization: Bearer $PTT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query":"<用户原话>"}'
```

立即返回：

```json
{
  "job_id": "<job-id>",
  "status": "queued",
  "status_url": "/v1/query/status/<job-id>",
  "created_at": "2026-08-22T12:00:00+00:00"
}
```

每 1–2 秒轮询一次状态地址；成功后读取 `reply`，失败后读取 `error`。成功的后台任务也会进入 `/v1/history`。

### 2. 查看最近历史

```bash
curl -sS "$BASE/v1/history" \
  -X POST \
  -H "Authorization: Bearer $PTT_API_KEY" \
  -H 'Content-Type: application/json'
```

返回最近 20 条按时间倒序的问答记录：

```json
[
  {
    "session_id": "<record-id>",
    "transcript": "我把钥匙放哪里了？",
    "reply": "钥匙在玄关托盘里。",
    "created_at": "2026-08-22T12:00:00+08:00"
  }
]
```

### 3. 查看全部长期记忆

```bash
curl -sS "$BASE/v1/memories" \
  -X POST \
  -H "Authorization: Bearer $PTT_API_KEY" \
  -H 'Content-Type: application/json'
```

返回当前用户可访问的全部记忆条目：

```json
[
  {
    "id": "<memory-id>",
    "memory": "护照在白柜子第二层。",
    "created_at": "2026-08-22T12:00:00+08:00"
  }
]
```

## 错误处理

| 状态 | 含义 | 处理 |
| --- | --- | --- |
| `401` | 缺少 Authorization 或 key 不正确 | 检查调用方配置，不要打印 key |
| `422` | 请求格式错误 | 确认 `query` 非空且 JSON 合法 |
| `500` | 服务端执行或写入失败 | 保留原始错误提示，稍后重试 |
| `502` | 上层处理服务暂时不可用 | 稍后重试，不要静默编造答案 |

## 使用边界

- 只把用户原话发给 `/v1/query`，让服务端决定是查询还是记录。
- 只有明确要求“记住”“记录”“保存”时才应新增记忆。
- 需要浏览已有内容时用 `/v1/history` 或 `/v1/memories`。
- 回答必须依据接口返回内容；没有证据时明确说找不到，不要猜测。
