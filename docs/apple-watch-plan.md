# Apple Watch 语音助手 — 设计计划

> 2026-10-02 起草。开发在 worktree `/Users/weiwang/.zcode/worktrees/voice-assistant/apple-watch`（分支 `zcode/apple-watch`）。

## 目标

在 Apple Watch 上点击一个按钮即可对话：录音 → 服务端识别并按现有 fast-path 回答 → 手表显示（并朗读）结果。

## 现状调研结论（方案依据）

- 后端已有 FastAPI：`press_to_talk/api/main.py`（默认端口 10031），`POST /v1/chat` 接 `{query}` 返回 `{reply, memories, ...}`，Bearer `PTT_API_KEY` 认证（`press_to_talk/api/auth.py`），超时 `PTT_CHAT_TIMEOUT_SECONDS`（30s）。
- **没有音频上传端点**：目前录音→ASR 都在客户端做（`press_to_talk/core.py` 录 16kHz 单声道 WAV → `press_to_talk/audio/stt.py: run_stt()` multipart POST 到 `PTT_STT_URL` 的 OpenAI 兼容 `/audio/transcriptions`）。
- TTS 仅本地（`press_to_talk/audio/tts.py` 调 qwen-tts CLI 播放到本机设备），API 服务端以 `--no-tts` 运行，无 TTS 端点 → Watch 用系统 `AVSpeechSynthesizer`（watchOS 可用，zh-CN）。
- 网络：局域网 `docker.home:10031`；公网反向代理 HTTPS `https://va-dev.soj.myds.me:1443/`（Synology DDNS）→ Watch 在家、在外都能连。
- 部署：`scripts/deploy.sh`（ssh docker + docker compose up -d --build）；远程 `.env` 已含 `PTT_STT_URL/TOKEN`，无需新密钥。
- 子项目惯例：`mac_gui/`、`memo_web/` 均为仓库平级目录；测试放 `tests/`；提示词/配置在根 `workflow_config.json`。

## 总体架构

```
Apple Watch (watchOS SwiftUI, 独立 App watch_app/)
  点击大按钮 → AVAudioRecorder 录 16kHz/单声道/16bit WAV → 再点（或静音自动）停止
  → POST {server}/v1/ask-audio   multipart: file=*.wav, Authorization: Bearer PTT_API_KEY
        服务端: run_stt() 转写 → try_fast_memory_chat()（TypeSafe 二分 → Memos 直写/查询 → Harness）
                → 返回 None 则回退 Harness Agent 兜底
        返回 {transcript, reply, memories, debug_info}
  ← 显示回复文本 + AVSpeechSynthesizer 朗读（zh-CN）
```

### 关键决策

1. **ASR 放服务端**（新增音频端点），不在 Watch 上做语音识别。理由：STT 服务 `docker.home:8046` 在局域网，Watch 出门直连不到；服务端转写可 100% 复用现有 ASR 配置与录音格式（16kHz 单声道 WAV）。Watch 端只负责录音与上传。
2. **独立 watchOS App，不做 iOS 伴侣 App**：watchOS 支持独立 App，个人设备 Xcode 直装即可，省一整个 iOS 工程。
3. **认证复用 `PTT_API_KEY`**（Bearer），不新增密钥体系。
4. **交互 = 点一下开始、再点一下结束**（贴合 press-to-talk 传统；手表上比长按稳）；静音自动停止作为打磨项。
5. MVP 走同步 `/v1/ask-audio`（30s 超时兜底）；Harness 慢查询的异步化（`/v1/query/async` 轮询）列为可选增强。

## 分阶段任务

### 阶段 1 — 服务端音频问答端点（约半天）
- `press_to_talk/api/main.py`（或新模块 `press_to_talk/api/audio.py` 挂 router）：
  - `POST /v1/ask-audio`：multipart `file`（WAV）→ 写临时文件 → `run_stt()` → `try_fast_memory_chat()` → 为 None 时回退 `DeepSeekHarnessClient.query()` → 返回 `{transcript, reply, memories, debug_info}`。
  - 错误语义化：录音过短（<0.5s）、STT 失败、上游超时分别返回 4xx/5xx + message。
  - 超时沿用 `PTT_CHAT_TIMEOUT_SECONDS`。
- 测试 `tests/test_api_ask_audio.py`（仿 `tests/test_api_server.py`：mock `run_stt` 与 `try_fast_memory_chat`，覆盖 fast 命中 / 回退 / STT 失败三路）。
- 验证：pytest 相关用例 + 冒烟 `curl -F file=@/tmp/voice_input.wav -H "Authorization: Bearer $PTT_API_KEY" http://127.0.0.1:10031/v1/ask-audio`。

### 阶段 2 — watchOS App MVP（约 1–2 天）
- `watch_app/project.yml`（XcodeGen 生成 .xcodeproj，独立 watchOS App，SwiftUI，最低 watchOS 10）：
  - `VoiceView.swift`：主界面大按钮，四态（待机 / 录音中·脉冲动画 / 思考中 / 结果展示）。
  - `Recorder.swift`：`AVAudioSession(.record)` + `AVAudioRecorder`（16000 Hz、1 声道、LinearPCM 16bit、WAV）；最短录音校验。
  - `APIClient.swift`：URLSession multipart 上传 + Bearer 头，40s 超时，JSON 解码。
  - `SettingsView.swift`：服务器 URL（默认公网 `https://va-dev.soj.myds.me:1443`；可切局域网 `http://docker.home:10031`）与 API Key（UserDefaults；http 联调需 ATS 例外 `NSAllowsLocalNetworking`）。
- 验证：`xcodebuild -scheme WatchApp -destination 'generic/platform=watchOS Simulator' build`；真机 Xcode 直装实测。

### 阶段 3 — 体验打磨（约 1 天）
- `AVSpeechSynthesizer` 朗读回复（zh-CN），再次点击打断。
- 静音自动停止：metering `averagePower` 低于阈值持续 ~1s 自动结束录音。
- 触觉反馈：`WKInterfaceDevice.current.play(.start/.success)` 区分开始录音 / 收到回答。
- 错误呈现：网络不通 / 超时 / 未识别到语音分开提示，配重试按钮；超长回复可滚动。

### 阶段 4 — 可选增强（按需）
- Harness 慢查询走 `POST /v1/query/async` + `GET /v1/query/status/{job_id}` 轮询，避免同步超时。
- WidgetKit 复杂功能一键直达录音页；App Intent 接入系统。
- "仅记录"显式按钮（fast-path 的 record 意图已自动覆盖，此为免识别的直写入口）。
- 离线兜底：watchOS 10 `SFSpeechRecognizer` 本地识别 + 纯文本 `/v1/chat`。

## 风险与注意

- watchOS 前台限制：录音与请求需保持前台（抬腕点按场景天然满足）；请求中熄屏可能挂起，恢复后提示重试。
- 公网 HTTPS 证书需有效（Synology 反代证书续期），Watch 端 ATS 对自签/HTTP 需例外。
- ≤8s 体验目标：Watch 端要有"思考中"过渡态，服务端 30s 超时兜底。
- 上线走现有 `scripts/deploy.sh`（deploy-to-docker 技能）；远程 `.env` 无需新增配置。

## 下一步

在 worktree 内从阶段 1 开始实施；每阶段结束按 AGENTS.md 验证（pytest / swift&xcodebuild 构建）。
