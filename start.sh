#!/bin/bash

# 启动 ptt-api (前台运行)
scripts/start_pocketbase.sh&
POCKETBASE_PID=$!

# API authentication and storage both depend on PocketBase. Wait for the
# embedded service before starting workers, otherwise a brief startup race can
# leave a healthy-looking API that returns authentication errors.
POCKETBASE_READY=0
for _ in $(seq 1 30); do
    if curl -fsS --max-time 1 http://127.0.0.1:18090/api/health >/dev/null 2>&1; then
        POCKETBASE_READY=1
        break
    fi
    if ! kill -0 "$POCKETBASE_PID" 2>/dev/null; then
        wait "$POCKETBASE_PID"
        exit 1
    fi
    sleep 1
done

if [ "$POCKETBASE_READY" -ne 1 ]; then
    echo "PocketBase did not become ready within 30 seconds" >&2
    kill "$POCKETBASE_PID" 2>/dev/null || true
    wait "$POCKETBASE_PID" 2>/dev/null || true
    exit 1
fi

# 根据环境变量 PTT_API_RELOAD 决定是否开启自动重载 (Docker 环境下建议默认关闭以降低 CPU)
RELOAD_FLAG=""
if [ "${PTT_API_RELOAD}" = "1" ]; then
    RELOAD_FLAG="--reload"
    echo "Starting ptt-api with --reload (Development Mode)" >&2
else
    echo "Starting ptt-api without --reload (Production Mode)" >&2
fi

uv run ptt-api ${RELOAD_FLAG} -v
