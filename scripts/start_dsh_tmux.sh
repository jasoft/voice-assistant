#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
session_name="${DSH_TMUX_SESSION:-voice-assistant-dsh}"
host="${DSH_WEB_HOST:-0.0.0.0}"
port="${DSH_WEB_PORT:-3080}"

if ! command -v tmux >/dev/null 2>&1; then
  printf '未找到 tmux，无法在用户会话中持久运行 dsh。\n' >&2
  exit 1
fi

if tmux has-session -t "$session_name" 2>/dev/null; then
  printf 'dsh 已在 tmux 会话 %s 中运行。\n' "$session_name"
  exit 0
fi

tmux new-session -d \
  -s "$session_name" \
  -c "$project_root" \
  "$project_root/scripts/start_dsh.sh" --host "$host" --port "$port"

printf 'dsh 已启动在 tmux 会话 %s：%s:%s\n' "$session_name" "$host" "$port"
