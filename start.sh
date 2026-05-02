#!/bin/bash

# 启动 ptt-api (前台运行，开启自动重载)
scripts/start_pocketbase.sh&
uv run ptt-api --reload -v
