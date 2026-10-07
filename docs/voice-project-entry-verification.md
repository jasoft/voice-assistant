# 语音助手项目任务入口：验证报告

日期：2026-10-07（本轮更新至 v0.1.48 之后）。执行：ZCode / GLM-5.3-Flash，Codex 只读监督。
计划：`docs/voice-project-entry-plan.md`（六阶段）。
功能与修复提交：`a72f9c2335`（功能）、`cd3519c1d6`（task_id 指代 + 客户端 request_id）、`5e6d1fd38b`（写者锁 waiting）、本轮（事件确认语义 + agy CLI 路线）。全部已合并 main 并推送 origin。
部署版本：v0.1.46 → v0.1.47 → v0.1.48（均经 `./scripts/deploy.sh`，本轮另有新部署，见文末）。

## 阶段完成表（当前真实状态）

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| 1. 确认原生接入 | ✅ Codex 已验证；Antigravity 部分完成 | Codex：官方 app-server JSON-RPC，线程在原生侧栏列表可见（监督独立确认 thread `01a1121d-3909-7862-bb90-4ec91120d4fd` 及生产 thread `01a1124b`）；`codex exec` 线程不可见已实测排除。Antigravity：按大王确认的 **agy CLI 免登录直接可用** 路线执行（见下），CLI 会话**不进桌面列表**为如实标注的未达条件 |
| 2. 项目注册 | ✅ 已验证 | `config/projects.json`：voice-assistant（语音助手等别名）、comfyui（千问/Qwen Image Studio 等），含路径/默认工具/工具可用性；`tool_availability` 当前 codex=true、antigravity=true（大王确认 CLI 路线后启用） |
| 3. 语义分流 | ✅ 已验证 | typesafe `task` 意图 + `prompts.project_task_parse` 一次 ChatCompletion 解析；模型可从最近任务上下文点名 `task_id`（指代修复）；解析失败/未登记项目/不可用工具明确澄清，不退 Harness 假执行 |
| 4. 远程转交 | ✅ 已验证 | `/v1/project-tasks*` 端点族 + 心跳；领取限配置用户；失联任务如实转 failed；JSON 存储挂 `data/` 卷；执行器主动领取，无新增公网端口 |
| 5. 进度与继续 | ✅ 已验证 | 消费进度（applied_followups/requirement_applied）**服务端确认后才执行下一轮**，崩溃/重启不重放；终态事件**重试直至确认**；追加沿用原会话；停止走原生中断 |
| 6. 验证与交付 | ✅ 已验证（桌面端执行中项见"未验证"） | 定向测试全绿；生产 /v1/chat 全链路（转交/续接/进度/停止/幂等）验证；执行器 launchd 常驻 |

## Codex 路线证据（只读短任务）

本地端到端：任务 `fe2d38bc` → 原生会话 `01a11245-9327-7a…` → "voice-assistant / main"；追加同会话续接复述分支名；运行中停止 `91d6a7f4` → cancelled。幂等：同 idempotency_key 重试返回 IDENTICAL。

跨进程一致性（前置检查，thread `01a11242-…`）：执行器进程记住代号 ALPHA → 释放锁 → 独立进程手动改为 ALPHA-7 → 执行器新进程续接答出 ALPHA-7。

### 原生 TUI 手动追加验收（生产实景）

在 Codex 原生 TUI（`codex resume <thread_id>`，与桌面端同 app-server 后端与磁盘线程库）对生产会话 `01a1124b-9442-7c73-9c5d-1318ae9b4713`：

1. TUI 打开即显示语音转交的两轮历史（45 个测试 / test_api 9 个）；
2. TUI 键入手动追加"只读统计 test_project 开头的测试文件" → 答 `2`，标题自动更新；
3. 语音续接 → 任务 `550333e4` 完成，回复"45 个测试文件；以 test_api 开头的约有 9 个。**手动追加那一问的答案是 2**"——执行器与原工具界面共享同一会话上下文。

**明确声明**：以上为 TUI 与双进程证据，属于前置检查；**ChatGPT 桌面端内嵌 Codex 界面在"正在执行时手动追加/停止"的验收未通过**——桌面 UI 的自动化控制被本工具安全限制禁止，未用替代技术绕过，由 Codex 监督独立验证真实桌面界面。已知原生限制：执行器回合进行期间，写者锁使任何其他界面（含桌面端）无法写入同一线程；回合结束锁即释放。语音侧停止在执行中始终可用（`turn/interrupt` 实测）。

## Antigravity 路线（大王确认 CLI 免登录直接可用）

- **执行入口**：现有已登录 agy CLI（1.3.0，免登录直接执行），`tool_availability.antigravity=true` 已启用；**不要求网页 OAuth、不要求账号切换、不以 --remote-control 为前提**（此前等待网页授权的指导已由大王撤回）。
- **已实测能力**：原生 conversation ID 创建（`agy --project X --print --output-format json` → `conversation_id`）；同会话续接（`--conversation <id>`，模型正确复述上一轮回复）；headless 权限拒绝如实呈现（`denied_actions` → failed 并注明需 settings.json permissions.allow 或交互方式，不自行扩大 allow 规则）；停止（stop_check 轮询 1s，终止子进程 → cancelled）；增量追加（followup 只发新增，消费进度确认后才执行）；请求幂等（与 Codex 共用 request_id 机制）。
- **未达条件（如实标注）**：CLI 会话不出现在 Antigravity 桌面/IDE Agent Manager 会话列表（52 条列表实测不含当日 3 个 CLI 会话 ID，CLI 无 list 命令）。**不能声称已在桌面可见；执行时也不静默换 Codex**。大王可继续用 agy 官方交互接口（`agy --conversation <id>` 交互续聊）查看与续聊。
- 探针证据：`agy --project voice-assistant --remote-control --print` 会话 `8e3e4b79-…` 返回 rc-probe-ok（remote-control 探针存在，但按大王指示不作为必要前提）；remote-control daemon 为 active 且已认证（ja.important@gmail.com，实例 macmini-lan-home-noble-orbit）。

## 执行器事件回传语义（本轮修复）

- **消费进度确认后才执行**：`applied_followups`/`requirement_applied` 事件回传未获服务端确认（重试约 1 分钟）时，本轮**不执行**，任务保持 running，交由服务端失联清理如实呈现——崩溃/重启不会重放已消费内容（负例测试：进度始终失败 → `run_turn` 零调用）。
- **终态事件重试直至确认**：completed/failed/cancelled 事件按 30s 间隔重试至 `PROJECT_EXECUTOR_TERMINAL_EVENT_MAX_WAIT_SECONDS`（默认 1800s），期间独立心跳保持执行器在线，避免 stale sweep 与重试竞态；超时未确认才交由 stale sweep 如实兜底（负例测试：前两次失败第三次成功 → 必须重试）。
- **resume 错误分类修正**：仅 `active writer` 视为原工具占用（重试后转 waiting）；任何其他错误按真实原因 failed，不再误报"原工具占用"（负例测试：`thread not found` → failed 且 error 含真实原因）。

## 客户端 request_id（网络重试幂等闭环）

- 服务端 `/v1/chat` 可选 `request_id`；followup/stop 请求级幂等键（`action_keys`，上限 20）。
- Mac GUI：`AppModel.performRemoteQuery` 每次口述生成 UUID → `VAClient.chat(requestID:)`（Release 构建通过）。
- Apple Watch：`VoiceSession.reset()` 每轮口述重新生成、识别重试沿用 → `APIClient.streamChat(requestID:)`（源码校验通过；真机按既有 install.sh 流程，未实测）。
- 生产幂等实测：同一 request_id 重试 → "这条补充要求之前已经转给任务550333e4了，不会重复添加"；指代选择实测正确选中 `550333e4`。

## 生产链路证据（v0.1.48 前后）

| 场景 | 结果 |
| --- | --- |
| 转交 tests 测试文件数量任务（prod-verify-1） | 任务 `550333e4` → 会话 `01a1124b` → "45 个测试文件" |
| 语音续接 test_api 数量 | 同会话 → "约有 9 个" |
| 进度查询 | 真实状态播报 |
| 停止任务 `18040ef5` | cancelled："已按用户要求中断 Codex 原生会话" |
| 千问别名 → comfyui | 任务 `c333922c` 在 comfyui 目录执行 → "comfyui" |
| 未登记项目"淘宝" | 澄清，未转交 |
| 显式 Antigravity（当时禁用） | 明确拒绝（现按大王指示已启用 CLI 路线） |
| TUI 手动追加 → 语音续接 | "手动追加那一问的答案是 2"（见上） |
| 写者锁 waiting | TUI 持锁 → 重试 3 次转 waiting → 关闭后续接完成 |
| 回归 chat/query/record | 均走原路径 |

Apple Watch 真机与 Mac GUI 未实测（走同一 `/v1/chat` 入口，服务端已验证）。

## uv.lock 溯源说明

会话开始时 main 检出的未提交 uv.lock 改动，内容为版本行 `0.1.44 → 0.1.45`（对应 v0.1.45 release 提交漏同步 lock）。功能提交 `a72f9c2335` 的 uv sync 产物包含同一行变更，合并后该改动以提交形式保留在 main，**无任何用户修改被丢弃**。其后 `4b7a632f3b`、`2156f97f33`（worktree-merge-clean 自动提交）与 `5e6d1fd38b` 中的 uv.lock 变更均为随 VERSION bump 的版本行对齐（v0.1.46/0.1.47/0.1.48），无他人内容混入；main 工作区当前干净。

## 未完成 / 未验证项（如实）

1. **Antigravity 桌面/IDE 列表互通：未达成**（无受支持接口，实测排除）；agy CLI 为大王确认的执行面，会话可用 `agy --conversation <id>` 续聊。
2. **Codex 桌面端"执行中追加/停止"未通过验收**：桌面 UI 自动化被工具安全限制禁止；已有 TUI/双进程前置检查证据，待监督在真实桌面界面独立验证。
3. **Watch 真机 / Mac GUI 端到端未实测**（客户端 request_id 代码已就绪）。
4. agy headless 的工具权限拒绝路径已如实呈现，但未配置任何 allow 规则（保持现有权限，不扩大）；需要执行命令的 agy 任务会 failed 并说明。
5. 执行器心跳窗口 180s、失联判定 900s、终态重试 30min、写者锁重试 3×10s 均为可调默认值，未经长时间 soak 测试。
