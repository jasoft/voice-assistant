# Apple Watch 语音助手

在手表上点按说话 → 服务端转写并按 fast-path/Harness 链路回答 → 手表显示并朗读结果。

首次启动、离开 App 后重新进入前台时自动开始录音；前台内暗屏（inactive）、抬腕亮屏及关闭设置保留当前对话。进入后台只标记下次录音，不清空回答或停止播放。回复页也可点麦克风开始下一次录音。App 声明后台音频模式。

watchOS 的公开生命周期没有单独的“点击图标”事件：使用 background → active 区分重新进入 App 与 inactive → active 的抬腕亮屏。系统因“返回时钟”超时将 App 转入后台后，下一次恢复前台也会自动录音。

## 架构

- `WatchApp/`（SwiftUI，独立 watchOS App，最低 watchOS 10）：
  - 录音 `Recorder.swift`：16kHz/单声道/16bit PCM WAV（与服务端 ASR 格式一致）。
  - 上传 `APIClient.swift`：`POST {server}/v1/ask-audio`（multipart，Bearer `PTT_API_KEY`）。
  - 界面 `VoiceView.swift`：大按钮四态（待机/录音/思考/结果）；回复用系统 `AVSpeechSynthesizer` 朗读（zh-CN）。
  - 设置 `SettingsView.swift`：服务器地址与 API Key（默认值来自 `.env`，见下）。
- 服务端：`press_to_talk/api/main.py` 新增 `POST /v1/ask-audio`，STT 转写后复用 `/v1/chat` 链路。

## 构建与安装

依赖：Xcode、[XcodeGen](https://github.com/yonaskolb/XcodeGen)（`brew install xcodegen`）。

```bash
# 模拟器构建（验证编译）
xcodegen generate
xcodebuild -project VoiceAssistantWatch.xcodeproj -scheme WatchApp \
  -destination 'generic/platform=watchOS Simulator' build

# 安装到 Apple Watch（需 iPhone 已在 Xcode 中配对本机）
./install.sh
```

`gen-secrets.sh` 会从仓库根 `.env` 读取 `PTT_API_KEY` 写入 `WatchApp/Sources/Secrets.swift`
（已 gitignore），作为手表端默认 API Key；默认服务器为生产公网反代地址
`https://va.soj.myds.me:1443`（群晖反代 → docker.home:10031；`va-dev` 子域指向
MacBook Air 开发机，勿用作默认值），可用 `WATCH_SERVER_URL` 覆盖，或在手表 App
「设置」里改。

## 服务端测试

```bash
uv run pytest tests/test_api_ask_audio.py -q
```
