#!/bin/bash

# 启动 ptt-api (前台运行)
scripts/start_pocketbase.sh&

# 根据环境变量 PTT_API_RELOAD 决定是否开启自动重载 (Docker 环境下建议默认关闭以降低 CPU)
RELOAD_FLAG=""
if [ "${PTT_API_RELOAD}" = "1" ]; then
    RELOAD_FLAG="--reload"
    echo "Starting ptt-api with --reload (Development Mode)"
else
    echo "Starting ptt-api without --reload (Production Mode)"
fi

uv run ptt-api ${RELOAD_FLAG} -v
