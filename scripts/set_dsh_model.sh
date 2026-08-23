#!/usr/bin/env bash

set -euo pipefail

model="${1:-fast}"
fast_max_tokens="${DSH_FAST_MAX_TOKENS:-256}"
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
  in_block && /^[^[:space:]#]/ {
    if (!replaced_effort) {
      print "  reasoningEffort: off"
    }
    in_block = 0
    print
    next
  }
  in_block && $1 == "model:" {
    print "  model: " model
    replaced = 1
    next
  }
  in_block && $1 == "reasoningEffort:" {
    print "  reasoningEffort: off"
    replaced_effort = 1
    next
  }
  { print }
  END {
    if (!replaced) {
      exit 10
    }
    if (in_block && !replaced_effort) {
      print "  reasoningEffort: off"
    }
  }
' "$settings" > "$tmp"

mv "$tmp" "$settings"

# Groq's free TPM budget counts input plus requested max output. The minimal
# memory agent only needs short tool calls/replies, so keep fast safely below
# the 8k limit instead of inheriting a coding-agent-sized 6000-token ceiling.
# The Qwen gateway defaults to thinking even when Harness omits the parameter.
# Declare Off as its supported wire value "none" and select it by default.
if [[ "$model" == "fast" ]]; then
  awk -v max_tokens="$fast_max_tokens" '
    /^[[:space:]]*- id:[[:space:]]*fast[[:space:]]*$/ {
      match($0, /^[[:space:]]*/)
      fast_indent = substr($0, RSTART, RLENGTH)
      fast_indent_length = RLENGTH
      print
      print fast_indent "  maxTokens: " max_tokens
      print fast_indent "  reasoningEfforts:"
      print fast_indent "    off: \"none\""
      print fast_indent "    high: high"
      print fast_indent "  compat:"
      print fast_indent "    supportsDeveloperRole: false"
      print fast_indent "    supportsReasoningEffort: true"
      in_fast = 1
      next
    }
    in_fast {
      match($0, /^[[:space:]]*/)
      lead_length = RLENGTH
      if ($0 ~ /^[[:space:]]*- / || ($0 !~ /^[[:space:]]*$/ && lead_length <= fast_indent_length)) {
        in_fast = 0
        print
        next
      }
      if (removing_block) {
        if ($0 ~ /^[[:space:]]*$/) {
          next
        }
        if (lead_length <= block_indent_length) {
          removing_block = 0
        } else {
          next
        }
      }
      if ($1 == "maxTokens:") {
        next
      }
      if ($1 == "reasoningEfforts:") {
        removing_block = 1
        block_indent_length = lead_length
        next
      }
      if ($1 == "compat:") {
        removing_block = 1
        block_indent_length = lead_length
        next
      }
      print
      next
    }
    { print }
  ' "$settings" > "$tmp"

  mv "$tmp" "$settings"
  printf 'DeepSeek Harness fast maxTokens set to %s and default reasoning set to none in %s\n' "$fast_max_tokens" "$settings" >&2
fi

printf 'DeepSeek Harness default model set to %s in %s\n' "$model" "$settings" >&2
