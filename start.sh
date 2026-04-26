#!/bin/bash
# 启动 sqlite_web (后台运行)
uvx sqlite_web data/voice_assistant_store.sqlite3 &
# 启动 ptt-api (前台运行)
uv run ptt-api
