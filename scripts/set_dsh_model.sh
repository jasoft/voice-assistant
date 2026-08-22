#!/usr/bin/env bash

set -euo pipefail

model="${1:-free}"
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
settings="${DSH_SETTINGS:-$project_root/config/deepseek-harness/runtime/settings.yaml}"

if [[ ! -f "$settings" ]]; then
  printf 'DeepSeek Harness settings not found: %s\n' "$settings" >&2
  exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

awk -v model="$model" '
  /^agent-default-model:/ {
    print
    in_block = 1
    next
  }
  in_block && $1 == "model:" {
    print "  model: " model
    replaced = 1
    in_block = 0
    next
  }
  { print }
  END {
    if (!replaced) {
      exit 10
    }
  }
' "$settings" > "$tmp"

mv "$tmp" "$settings"
printf 'DeepSeek Harness default model set to %s in %s\n' "$model" "$settings" >&2
