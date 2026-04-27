#!/bin/bash
# 启动 sqlite_web (后台运行，通过包装脚本加载 libsimple.so 扩展)
uv run python /app/patch_sqlite_web.py /app/data/voice_assistant_store.sqlite3 --host 0.0.0.0 --port 8080 &
# 启动 ptt-api (前台运行，开启自动重载)
uv run ptt-api --reload
