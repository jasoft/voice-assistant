# Chat acceptance runner

`run_chat_acceptance.py` 执行 `tests/scenarios/chat_acceptance.json` 中的 40 条用例，每条 3 次。当前保存结果见 `docs/reports/chat-acceptance-2026-10-04/`。

## 环境要求

- 在与生产镜像一致的 `voice-assistant:local` 内运行，使用 `/app/.venv/bin/python`。
- 单独创建 Docker 测试网络和空数据的 `neosmemo/memos:0.30.0`，网络别名必须为 `test-memos`。不挂载生产数据。runner 会创建临时测试管理员，只接受 `MEMOS_BASE_URL=http://test-memos:5230`。
- 单独启动 Harness，网络别名为 `test-harness`，使用生产一致的 settings、`chat-fast` preset 和所需模型凭据；会话存储使用临时目录。确认 `CLIPROXYAPP_API_KEY` 注入该实例，不能只复制 `.credentials.yaml` 后假设该 key 已加载。
- `/test` 挂载临时工作目录，放入 runner、`chat_acceptance.json` 和 Harness 启动时产生的 token 文件 `harness-token`。保留 API 的真实 OpenAI/Cloudflare 配置；凭据通过权限为 0600 的临时 env-file 注入，不输出内容。
- 设置 `PTT_QUERY_BACKEND=deepseek-harness`、`PTT_HARNESS_API_URL=http://test-harness:3080`、两个 Harness preset 为 `chat-fast`、两个 timeout 为 `30`；`PTT_API_KEY` 与 `PTT_USER_ID` 使用测试值。将 `MEM0_BASE_URL`、`PTT_PB_URL` 指到 `http://127.0.0.1:1`，避免访问生产存储。

运行：

```sh
/app/.venv/bin/python /test/run_chat_acceptance.py
```

runner 使用 FastAPI 的 ASGITransport，仍经过真实端点、认证和中间件，但不启动公开测试端口。仅历史持久化重定向为内存列表；正常分类、生成、Harness 和 Memos 都真实执行。F01–F04 在测试进程内注入指定故障。日志在 stderr，进度在 stdout；每次完成后立即追加证据至 `/test/results.jsonl`。

`ACCEPTANCE_START_CASE=Q01` 可从某例继续，供修正隔离环境后保留已经有效的前置样本。使用前先移走无效结果、重建空 Memos，确保结果每例恰好三条。不能把重复或无效样本混入正式报告。该选项不会自动决定哪些样本有效。

## 审阅与报告

对每条实际响应、读回正文、路由和工具事件进行语义审阅，将结论保存为 `semantic-review.json`，以用例 ID 和重复次数索引，包含 `pass`、`note`。失败项解释保存为 `findings.json`。不要用示例文本逐字比对，也不要把自动结构检查通过等同于语义通过。

在发布证据前，将外部搜索响应的正文替换为内容哈希与去重来源元数据，保留调用参数及实际响应的合成测试文本。不要保存或发布任何凭据。

```sh
uv run python scripts/testing/render_chat_acceptance_report.py docs/reports/chat-acceptance-2026-10-04
```

输出 Markdown、可筛选 HTML、JSON 汇总；渲染器校验 120 个唯一的用例/重复组合。正式用例必须三次均通过才算该用例通过，观察用例单独列出。

完成后确认测试 Memos 为零条，删除此次测试容器、网络、临时 Harness 存储、凭据和脚本副本，保留报告。不要重启生产服务。

临时 Memos 的用户创建及登录依据 [Memos v0.30 用户协议](https://github.com/usememos/memos/blob/v0.30.0/proto/api/v1/user_service.proto) 和 [鉴权协议](https://github.com/usememos/memos/blob/v0.30.0/proto/api/v1/auth_service.proto)。
