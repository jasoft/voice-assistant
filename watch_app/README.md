# Apple Watch 语音助手

在手表上点按说话 → 服务端转写并按 fast-path/Harness 链路回答 → 手表显示并朗读结果。

首次启动、离开 App 后重新进入前台时自动开始录音；前台内暗屏（inactive）、抬腕亮屏保留当前对话。进入后台只标记下次录音，不清空回答或停止播放。回复页也可点麦克风开始下一次录音。App 声明后台音频模式。

watchOS 的公开生命周期没有单独的“点击图标”事件：使用 background → active 区分重新进入 App 与 inactive → active 的抬腕亮屏。系统因“返回时钟”超时将 App 转入后台后，下一次恢复前台也会自动录音。

## 界面与流式回答

- 打开即录音；大麦克风按钮点按结束并发送，显示录音时长；右上角取消直接丢弃录音，回到初始麦克风页，不请求识别。
- 识别、思考和回答阶段，左上角麦克风随时打断并重录。思考时完整显示识别原话；首段回答出现后自动上滚到答案，原话保留在上方，可滚回检查。正文支持段落、行内 Markdown 与滚动，占满主体，无底部操作条。
- 播放/停止放在右上角，主界面不再显示设置入口。
- 新安装默认自动播报，尊重已有关闭设置。首段文字到达即准备语音；按句分段，首段无标点时最多缓存 400ms，再请求 `/v1/tts`；边收 24kHz/16bit/单声道 PCM 边播。剩余已到达的短句合并合成，避免每句话都等待一次网络往返。
- 停止只停止播报；再说会取消当前生成、停止音频并开始新录音。流中断保留部分文字并提示，禁止失败后重跑另一个模型造成重复或改口。识别失败保留临时录音供显式重试；开始新录音时清理。
- 旧服务器返回 JSON 时读取同一次响应兼容展示与播报，不重新发请求；旧服务器无法提供提前输出的体验。

## API 契约

Watch 先上传 WAV 到 `/v1/transcribe`，再发 `POST /v1/chat`：

```json
{"query":"语音识别文字", "stream":true, "response_style":"watch"}
```

`stream` 默认 `false`，原客户端仍收到完整 JSON。`true` 返回 `text/event-stream`，含注释心跳及以下事件；JSON 字段中的换行被转义，每个事件的数据为一行 JSON：

```text
event: delta
data: {"text":"新增的文字"}

event: done
data: {"reply":"完整回答", "action":"speak", "memories":[], "images":[], "query":"原问句", "debug_info":{}}

event: error
data: {"message":"回答中断，请重新提问"}
```

只有收到 `done` 才算成功。鉴权/参数错误在开始流之前返回普通 HTTP 错误；生成中的错误用 `error` 结束。连接关闭会取消生成任务并关闭上游流。取消无法撤销已提交的备忘写入，客户端不自动重试聊天请求。

普通回答、记忆查询和文本生成透传模型的真实 content 增量，不播报 reasoning。Memos 写入确认、仅提供最终结果的 Harness 工具路径在完成后发一个完整 `delta`，不模拟逐字输出。`response_style=watch` 的输出要求来自外部 `workflow_config.json` 的 `prompts.watch_answer`。

## 构建与安装

依赖：Xcode、[XcodeGen](https://github.com/yonaskolb/XcodeGen)（`brew install xcodegen`）。

```bash
# 模拟器构建（验证编译）
cd watch_app
./gen-secrets.sh
xcodegen generate
xcodebuild -project VoiceAssistantWatch.xcodeproj -scheme WatchApp \
  -destination 'generic/platform=watchOS Simulator' build

# 安装到 Apple Watch（需 iPhone 已在 Xcode 中配对本机）
./install.sh
```

`gen-secrets.sh` 会从仓库根 `.env` 读取 `PTT_API_KEY` 写入 `WatchApp/Sources/Secrets.swift`
（已 gitignore），作为手表端默认 API Key；默认服务器为生产公网反代地址
`https://va.soj.myds.me:1443`（群晖反代 → docker.home:10031；`va-dev` 子域指向
MacBook Air 开发机，勿用作默认值），可用 `WATCH_SERVER_URL` 覆盖，。主界面无需连接设置。

## 服务端测试

```bash
uv run pytest tests/test_api_ask_audio.py -q
```

## 本机模拟器验证

```bash
# 在仓库根运行真实 API 的确定性测试夹具：外部模型由延迟 SSE 替代，PCM 为测试音。
uv run python watch_app/Tests/simulator_server.py

# 另一个终端，构建/安装/启动到已启动的 Watch 模拟器。
VA_TEST_SERVER=http://localhost:10039 VA_TEST_KEY=watch-simulator-test \
  ./watch_app/run-simulator.sh --test-query '长回答测试'

uv run pytest tests/test_api_chat_stream.py tests/test_fast_chat_harness.py \
  tests/test_api_ask_audio.py tests/test_api_transcribe.py tests/test_api_tts.py -q
swiftc watch_app/WatchApp/Sources/SpeechTextBuffer.swift watch_app/Tests/SpeechTextBufferTests.swift -o /tmp/watch-speech-tests
/tmp/watch-speech-tests
```

夹具还支持“中断测试”“等待测试”。事件时间写入 `/tmp/va-watch-simulator-events.jsonl`，Watch 日志的 `text_first_delta`、`tts_request`、`audio_first_pcm`、`text_done` 可验证提前合成和提前播放。夹具不写个人备忘/会话历史。

模拟器测试参数仅 Debug 可用：`--test-query` 绕过麦克风，仍运行实际网络/显示/播报流程；`--test-idle` 检查待机页；`VA_TEST_LARGE_TEXT=1` 检查大字体。Release 不含这些覆盖。未传测试参数时按正式启动流程自动录音。实际触觉手感和抬腕行为仍需真机确认。
