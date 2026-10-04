#!/bin/bash
# Build and launch the Watch app on a local simulator. Does not install on hardware.
set -euo pipefail
cd "$(dirname "$0")"
./gen-secrets.sh
xcodegen generate
WATCH_SIM_ID="${WATCH_SIMULATOR_ID:-$(xcrun simctl list devices booted -j | uv run python -c 'import json,sys; d=json.load(sys.stdin); print(next((v["udid"] for k,vs in d["devices"].items() if "watchOS" in k for v in vs if v["state"]=="Booted"), ""))')}"
if [ -z "$WATCH_SIM_ID" ]; then
    echo '先在 Device Hub 启动一个 Watch 模拟器，或指定 WATCH_SIMULATOR_ID。' >&2
    exit 1
fi
WATCH_SIM_BUILD="${WATCH_SIMULATOR_BUILD_DIR:-/tmp/voice-assistant-watch-simulator}"
xcodebuild -project VoiceAssistantWatch.xcodeproj -scheme WatchApp -configuration Debug \
    -destination "platform=watchOS Simulator,id=$WATCH_SIM_ID" -derivedDataPath "$WATCH_SIM_BUILD" build CODE_SIGNING_ALLOWED=NO
xcrun simctl install "$WATCH_SIM_ID" "$WATCH_SIM_BUILD/Build/Products/Debug-watchsimulator/WatchApp.app"
if [ -n "${VA_TEST_SERVER:-}" ]; then export SIMCTL_CHILD_VA_TEST_SERVER="$VA_TEST_SERVER"; fi
if [ -n "${VA_TEST_KEY:-}" ]; then export SIMCTL_CHILD_VA_TEST_KEY="$VA_TEST_KEY"; fi
if [ -n "${VA_TEST_LARGE_TEXT:-}" ]; then export SIMCTL_CHILD_VA_TEST_LARGE_TEXT="$VA_TEST_LARGE_TEXT"; fi
xcrun simctl launch --terminate-running-process "$WATCH_SIM_ID" com.soj.voiceassistant.watch "$@"
open /Applications/Xcode.app/Contents/Applications/DeviceHub.app
