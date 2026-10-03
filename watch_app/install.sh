#!/bin/bash
# 构建 Release 版 watchOS App 并安装到已配对的 Apple Watch。
#
# 前提（一次性）：
#   1. iPhone 连接本机并信任，Xcode → Settings → Devices 里配对成功；
#   2. Apple Watch 与该 iPhone 配对（Xcode 设备列表会随之出现手表）；
#   3. 手表开启开发者模式（设置 → 隐私与安全性 → 开发者模式）。
# 之后每次更新代码，重跑本脚本即可。
#
# 可用环境变量：WATCH_DEVICE_ID 指定设备 UUID。
set -euo pipefail
cd "$(dirname "$0")"

./gen-secrets.sh
xcodegen generate

echo "==> 查找已连接的 Apple Watch"
xcrun devicectl list devices

# 先确定手表设备：用具体设备目的地构建，Xcode 才会把该手表注册进描述文件
# （generic 目的地生成的 profile 不含设备 UDID，安装时报 0xe8008012）
DEVICE_ID="${WATCH_DEVICE_ID:-}"
if [ -z "$DEVICE_ID" ]; then
  DEVJSON=$(mktemp)
  xcrun devicectl list devices --json-output "$DEVJSON" >/dev/null 2>&1 || true
  DEVICE_ID=$(python3 - "$DEVJSON" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
devices = (data.get("result") or {}).get("devices") or []
watches = []
for d in devices:
    hardware = (d.get("properties") or {}).get("hardware") or {}
    state = (d.get("properties") or {}).get("state") or {}
    if hardware.get("deviceType") != "appleWatch":
        continue
    if d.get("visibilityClass", state.get("visibilityClass")) == "simulators":
        continue
    watches.append(d)
if watches:
    print(watches[0].get("identifier", ""))
PY
) || true
  rm -f "$DEVJSON"
fi

DESTINATION='generic/platform=watchOS'
if [ -n "$DEVICE_ID" ]; then
  DESTINATION="id=$DEVICE_ID"
  echo "==> 使用设备目的地: $DEVICE_ID"
fi

echo "==> 构建 watchOS App（Release / 真机）"
xcodebuild -project VoiceAssistantWatch.xcodeproj \
  -scheme WatchApp -configuration Release \
  -destination "$DESTINATION" \
  -allowProvisioningUpdates \
  -derivedDataPath build clean build

APP_PATH=$(find build/Build/Products/Release-watchos -maxdepth 1 -name '*.app' | head -n 1)
echo "==> 产物: $APP_PATH"

if [ -z "$DEVICE_ID" ]; then
  echo "!! 未发现 Apple Watch。"
  echo "   请先用 Xcode 配对 iPhone（手表会随之出现在设备列表），"
  echo "   或设置 WATCH_DEVICE_ID=<设备 UUID> 后重跑本脚本。"
  exit 1
fi

echo "==> 安装到设备 $DEVICE_ID"
xcrun devicectl device install app --device "$DEVICE_ID" "$APP_PATH"
echo "==> 完成：在手表上打开「语音助手」，点按说话，再点一次发送。"
