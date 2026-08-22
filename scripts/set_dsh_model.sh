#!/usr/bin/env bash

set -euo pipefail

model="${1:-fast}"
fast_max_tokens="${DSH_FAST_MAX_TOKENS:-512}"
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

# Groq's free TPM budget counts input plus requested max output. The minimal
# memory agent only needs short tool calls/replies, so keep fast safely below
# the 8k limit instead of inheriting a coding-agent-sized 6000-token ceiling.
if [[ "$model" == "fast" ]]; then
  awk -v max_tokens="$fast_max_tokens" '
    /^[[:space:]]*- id:[[:space:]]*fast[[:space:]]*$/ {
      match($0, /^[[:space:]]*/)
      fast_indent = substr($0, RSTART, RLENGTH)
      print
      in_fast = 1
      next
    }
    in_fast && $1 == "maxTokens:" {
      match($0, /^[[:space:]]*/)
      print substr($0, RSTART, RLENGTH) "maxTokens: " max_tokens
      replaced = 1
      in_fast = 0
      next
    }
    in_fast {
      print fast_indent "  maxTokens: " max_tokens
      in_fast = 0
    }
    { print }
  ' "$settings" > "$tmp"

  mv "$tmp" "$settings"
  printf 'DeepSeek Harness fast maxTokens set to %s in %s\n' "$fast_max_tokens" "$settings" >&2
fi

printf 'DeepSeek Harness default model set to %s in %s\n' "$model" "$settings" >&2
