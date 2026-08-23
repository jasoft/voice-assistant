#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dsh_home="${DSH_HOME:-$HOME/.dsh}"
credentials_file="$dsh_home/.credentials.yaml"

# The same checked-in preset is used on macOS and in the Docker image. Point it
# at this checkout's wrapper unless a deployment deliberately overrides it.
export MEMO_API_SCRIPT="${MEMO_API_SCRIPT:-$project_root/scripts/memo_api.py}"

# Local DSH reads presets from DSH_HOME. Refresh user-installed project presets
# before launch so an existing ~/.dsh copy cannot keep yesterday's persona or
# skills alive. Docker mounts each preset directly, and has no config/ tree.
local_preset_root="$project_root/config/deepseek-harness/agent-presets"
if [[ -d "$local_preset_root" ]]; then
  mkdir -p "$dsh_home/.agent-presets"
  for preset_source in "$local_preset_root"/*; do
    [[ -d "$preset_source" ]] || continue
    preset_name="$(basename "$preset_source")"
    rm -rf "$dsh_home/.agent-presets/$preset_name"
    cp -R "$preset_source" "$dsh_home/.agent-presets/$preset_name"
  done
fi

# dsh-web-search-brave 0.2.2 checks the launch environment synchronously.
# Keep the canonical key in DSH's credential store, but also inject it into
# this one process so the provider is immediately usable after startup.
if [[ -z "${CLIPROXYAPP_API_KEY:-}" && -f "$credentials_file" ]]; then
  cliproxy_key="$(awk -F': ' '$1 == "CLIPROXYAPP_API_KEY" { print substr($0, index($0, ": ") + 2); exit }' "$credentials_file")"
  if [[ -n "$cliproxy_key" ]]; then
    export CLIPROXYAPP_API_KEY="$cliproxy_key"
  fi
fi

if [[ -z "${BRAVE_API_KEY:-}" && -f "$credentials_file" ]]; then
  brave_key="$(awk -F': ' '$1 == "BRAVE_API_KEY" { print substr($0, index($0, ": ") + 2); exit }' "$credentials_file")"
  if [[ -n "$brave_key" ]]; then
    export BRAVE_API_KEY="$brave_key"
  fi
fi

if [[ -z "${BRAVE_API_KEY:-}" ]]; then
  printf '未找到 BRAVE_API_KEY，请配置 %s 或导出 BRAVE_API_KEY。\n' "$credentials_file" >&2
  exit 1
fi

if [[ "$#" -eq 0 ]]; then
  set -- --port "${DSH_WEB_PORT:-3080}"
fi

exec pnpm --dir "$project_root/deepseek-harness" dsh web "$@"
