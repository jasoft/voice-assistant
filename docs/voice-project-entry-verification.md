# 语音助手项目任务入口：验证报告

日期：2026-10-07。执行：ZCode / GLM-5.3-Flash，Codex 只读监督。
计划：`docs/voice-project-entry-plan.md`（六阶段）。功能提交：`a72f9c2335`（已合并 main 并推送 origin）。部署版本：v0.1.46。

## 阶段完成表

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| 1. 确认原生接入 | ✅ 已验证 | Codex 走官方 app-server JSON-RPC（thread/start、turn/start、thread/resume、turn/interrupt）；`codex exec` 线程**不在**默认聊天列表（已实测排除）；app-server 创建的线程在 Codex 原生侧栏列表可见（监督方独立确认 thread `01a1121d-3909-7862-bb90-4ec91120d4fd`，项目目录 voice-assistant）。Antigravity 见"边界与未完成项" |
| 2. 项目注册 | ✅ 已验证 | `config/projects.json`：voice-assistant（语音助手等别名）、comfyui（千问/Qwen Image Studio 等别名），含路径/默认工具/工具可用性；服务端只用 id+别名，路径留在 Mac |
| 3. 语义分流 | ✅ 已验证 | typesafe intent 新增 `task`（criterion 外置 workflow_config.json）；`prompts.project_task_parse` 一次 ChatCompletion 解析动作/项目/工具/需求，模型只允许输出登记 id |
| 4. 远程转交 | ✅ 已验证 | `/v1/project-tasks*` 七个端点（创建/列表/查询/追加/停止/领取/事件 + 心跳），Bearer 认证沿用 PTT_API_KEY，领取限定配置用户；JSON 存储挂 `data/` 卷；Mac 执行器主动领取，无新增公网端口 |
| 5. 进度与继续 | ✅ 已验证 | 任务记录 native_session_id；追加沿用原会话（增量发送，消费序号持久化）；执行中停止走 `turn/interrupt`；失联任务如实转 failed |
| 6. 验证与交付 | ✅ 已验证 | 定向测试 59 项通过；生产 /v1/chat 全链路（转交/续接/进度/停止）验证；执行器已用 launchd 安装并常驻 |

## 定向测试

- `tests/test_project_tasks_store.py`（10）：状态机、幂等领取、追加续接、停止流、所有者隔离、领取限配置用户、失联 running 如实转 failed。
- `tests/test_api_project_tasks.py`（6）：认证、未登记项目 400、工具未启用 400、全生命周期、跨用户 404、追加/停止。
- `tests/test_project_entry.py`（18）：new/continue/status/stop/clarify、未登记项目澄清、显式不可用工具明确拒绝（不静默换 Codex）、注册表可用性门控、request_id 幂等（同 id 永不重复执行、新 id 窗口内也放行、按用户隔离）、/v1/chat 链路 request_id 贯通。
- `tests/test_fast_chat_task_intent.py`（2）：task 意图分流与回退。
- `tests/test_mac_executor.py`（12）：app-server 协议循环（按请求回应的假进程）、中断、增量 followup 文本构建（不重放已消费内容）、agy JSON 解析、注册表/可用性加载。
- 回归：`test_fast_chat_harness`、`test_harness_api_backend`、`test_api_endpoints_coverage`、`test_api_reminders`、`test_memo_web_fastpath`、`test_typesafe_client` 全部通过。
- 汇总：新增 59 项 + 既有回归套件全绿。

## 真实链路证据（只读短任务）

本地链路（127.0.0.1 端到端）：

- 任务 `fe2d38bc`（e2e-req-1）→ Codex 原生会话 `01a11245-9327-7a…` → completed："项目目录名：voice-assistant / Git 分支名：main"。同 idempotency_key 重试返回 IDENTICAL。
- 追加"用一句话复述分支名"→ 同一会话续接 → "我刚才检查到的分支名是 main"（增量发送，未重放原需求）。
- 运行中停止：任务 `91d6a7f4` → stop 请求 → `turn/interrupt` → cancelled："已按用户要求中断 Codex 原生会话"。

跨进程上下文一致性（监督要求的并发契约，thread `01a11242-ac0a-7882-b04a-0c7b22d9c30c`）：

1. 执行器进程创建会话并记住代号 ALPHA → 进程退出释放原生写者锁；
2. 模拟原工具的独立进程 resume 同一会话，手动改为 ALPHA-7；
3. 执行器新进程再次 resume，答出 `ALPHA-7` —— 语音侧与原工具共享同一上下文，无分叉。
   （关键机制：Codex 线程有跨进程"活跃写者"锁，执行器按任务生命周期启停 app-server 子进程，任务结束立即释放锁，原工具随时可接手。）

生产链路（https://va.soj.myds.me:1443/v1/chat，本机执行器领取）：

| 场景 | 结果 |
| --- | --- |
| "让 Codex 给语音助手项目做只读任务：tests 目录有几个测试文件"（prod-verify-1） | 任务 `550333e4` → 原生会话 `01a1124b-9442-7c73…` → completed："tests 目录下有 45 个测试文件" |
| 语音续接"继续刚才那个……test_api 开头的有多少" | 同一会话 `01a1124b` 续接 → "约有 9 个"（未重放原需求） |
| "刚才那个项目任务做得怎么样了？" | "任务550333e4（voice-assistant / Codex）当前状态：已完成。最新结果：……"（真实状态） |
| "把刚才那个任务停下来"（任务 `18040ef5` 运行中） | "已发送停止请求……" → cancelled："已按用户要求中断 Codex 原生会话" |
| 千问别名路由（prod-alias-1） | "千问"→ comfyui 项目，任务 `c333922c` 在 `/Users/weiwang/Projects/comfyui` 执行 → completed："comfyui"（voice-assistant 未被误改） |
| 未登记项目"淘宝项目"（prod-unknown-1） | 澄清："登记的项目里没有…你是想对哪个已登记项目操作？"，未转交 |
| 显式 Antigravity（prod-agy-1） | 明确拒绝："Antigravity 执行入口还没有通过原工具列表验收，暂时不能接任务"，未转交、未换工具 |
| 回归：普通聊天 / 查备忘 / 记录 | intent=chat（直连 LLM）、intent=query（CEL 命中 1 条）、intent=record（写入 Memos）均走原路径 |

Apple Watch 真机与 Mac GUI 未实测（大王休息，不唤醒设备）；它们走同一 `/v1/chat` 入口，服务端链路已在生产验证。

## 部署与推送状态

- 功能提交 `a72f9c2335` 已 fast-forward 合并 main 并推送 origin（`8147d29f..a72f9c23`）。
- `./scripts/deploy.sh` 在干净 deploy worktree 执行成功：版本 bump 至 v0.1.46，远程 docker.home 容器重建并启动（voice-assistant-1 / memo-web / deepseek-harness 均 Up）。
- Mac 执行器：launchd agent `com.voice-assistant.project-executor` 已安装（`~/Library/LaunchAgents`）并常驻运行，id=mac-macbookair.home，已配置 `PROJECT_EXECUTOR_SERVER_URL` 于本机 `.env`（含既有 `PTT_API_KEY`，无新增凭据）。
- 生产核验：`/healthy` 返回 v0.1.46；上述生产链路证据均来自真实生产入口。

## 边界与未完成项（如实记录）

1. **Antigravity IDE 列表互通：无受支持接口（已证实边界）**。agy 1.3.0 CLI 创建的会话（含 `--conversation` 续接验证可用）不会出现在本机 Antigravity IDE Agent Manager 会话列表：今天创建的 3 个 CLI 会话 ID（dde30116…、850140c2…、生产前本地测试会话）刷新后均不在 GUI 的 52 条列表中；CLI 无 list 子命令；`agy_remote.py` 依赖的 `gemini_notify.get_remote_control_info/get_session_remote_url` 已不存在（脚本过期）。受支持的原生远程入口是 `agy --remote-control` + antigravity.google.com 控制台（daemon 状态 active，实例 `macmini-lan-home-noble-orbit`，需一次人工 OAuth 授权）。因此 `config/projects.json` 中 `tool_availability.antigravity=false`：入口在解析阶段就明确拒绝并说明，执行器同样拒绝，**不以普通 CLI 冒充已验收的生产入口**。待官方提供列表互通接口或完成 OAuth 后，将配置翻为 true 即可启用（代码路径已就绪并有测试）。
2. **Codex daemon/proxy 未打通**：`codex app-server proxy` 连接共享 daemon 控制套接字后被对端关闭（无公开文档；不重启在用 Codex 服务的前提下未强行绕过）。执行器改为按任务生命周期管理独立 app-server 子进程，并以磁盘线程库 + `thread/resume` 保证跨进程一致性（上文实测）。正在执行的回合内，原工具侧对同一线程的写入会被原生写者锁拒绝（Codex 原生限制，桌面端/IDE 并发写同样受限）；回合结束后锁即释放，已实测原工具可接手。
3. **GUI/Watch 客户端 `request_id` 未下发**：服务端 `/v1/chat` 已支持可选 `request_id`（网络重试幂等），但 Mac GUI（Swift）与 Watch 端尚未改为每次口述生成稳定 UUID 并随重试复用；当前这两类客户端未带该字段时不做去重（行为与原来一致，不会误伤）。后续小改动：两个客户端请求体加 `request_id`。
4. **Watch 真机/Mac GUI 交互未实测**：服务端入口已验证，设备端体验（回执播报等）待大王醒后实测。
5. 执行器心跳窗口 180s、失联判定 900s、写者锁等待等参数为经验默认值（`PROJECT_EXECUTOR_*` 可调），未经长时间 soaked 测试。
