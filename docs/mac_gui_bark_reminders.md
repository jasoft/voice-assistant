# Mac GUI 手机提醒

Mac GUI 现在内置“手机提醒”窗口。提醒由 Upstash QStash 在云端保存和触发，到点后直接调用 Bark，所以 Mac GUI 关闭、Mac 关机或断网都不会阻止已经创建的提醒。

## 链路

```text
Mac GUI
  │ 创建一次性延迟消息
  ▼
Upstash QStash（notBefore）
  │ 到点 POST
  ▼
Bark HTTPS API
  ▼
iPhone 推送
```

## 配置

1. 注册/登录 [Upstash Console](https://console.upstash.com/)，创建或打开 QStash。
2. 复制 QStash 的 `QSTASH_TOKEN`。
3. 安装 Bark 并复制你自己的推送地址；也可以继续使用已有的完整 `BARK_URL`。
4. 在项目根目录 `.env` 加入：

```dotenv
QSTASH_TOKEN=你的QStash令牌
# 可选；区域控制台如果提供专用地址就填这里
# QSTASH_URL=https://qstash-us-east-1.upstash.io
BARK_URL=https://api.day.app/你的设备Key

# 可选
REMINDER_GROUP=Mac提醒
REMINDER_SOUND=minuet
```

如果不想把完整 Bark 地址写成一个变量，也可以改用：

```dotenv
BARK_SERVER=https://api.day.app/
BARK_DEVICE_KEY=你的设备Key
```

不要把真实 Bark Key 或 QStash Token 提交到 Git。

## 使用

1. 启动 Mac GUI。
2. 点击右上角的铃铛图标。
3. 输入内容和时间，点击“创建提醒”。
4. 打开页面或点击“刷新”时，GUI 会查询 QStash 日志并把本地状态同步为“已安排 / 已送达 / 发送失败 / 已取消”。已送达的提醒不会再显示取消按钮。

GUI 会把本地提醒记录写入项目根目录的 `.mac_gui_reminders.json`。这个文件只保存内容、时间和状态，不保存 QStash Token 或 Bark Key，并且已被 `.gitignore` 排除。

## 测试

Swift 包测试覆盖配置读取、Bark URL 转义、QStash 请求、消息 ID 解析、取消请求和本地记录：

```bash
cd mac_gui
swift test
```

## 自然语言创建

在聊天输入框直接说：

- “3分钟后提醒我起来走走”
- “明天早上8点提醒我开会”
- “每周五的9点提醒我吃药”

DeepSeek Harness 聊天 Agent 会调用项目内置的 `scripts/reminder_cli.py`。时间理解由大模型完成，配置在 `config/reminders/natural_language.json`；脚本负责换算时区、创建 QStash 一次性延迟消息或周期 cron schedule，并写入 GUI 的提醒列表。

支持一次性、每天、每周、每月提醒。每周/每天/每月时间按 `REMINDER_TIMEZONE`（默认 Asia/Shanghai）解释，并转换为 QStash 使用的 UTC cron。

## 真实验收

项目内置一条只输出状态、不输出密钥的验收命令：

```bash
uv run python scripts/test_bark_reminder.py
```

它会发送一条立即 Bark，再创建约 65 秒后的 QStash 延迟提醒。第一次接入后请确认 iPhone 实际收到两条推送：

1. “直连”消息：证明 Bark 地址可用。
2. “QStash 定时”消息：证明云端到点触发链路可用。

也可以自定义内容和延迟：

```bash
uv run python scripts/test_bark_reminder.py --message "测试提醒" --delay 120
```
