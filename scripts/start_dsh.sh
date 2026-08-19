#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dsh_home="${DSH_HOME:-$HOME/.dsh}"
credentials_file="$dsh_home/.credentials.yaml"

# dsh-web-search-brave 0.2.2 checks the launch environment synchronously.
# Keep the canonical key in DSH's credential store, but also inject it into
# this one process so the provider is immediately usable after startup.
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
