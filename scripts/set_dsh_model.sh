#!/usr/bin/env bash

set -euo pipefail

model="${1:-fast}"
resolved_model="$model"
fast_max_tokens="${DSH_FAST_MAX_TOKENS:-512}"
agent_preset="memo-minimal"
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
settings="${DSH_SETTINGS:-$project_root/config/deepseek-harness/runtime/settings.yaml}"

# "fast" is the voice assistant's latency-oriented logical route. The gateway's
# current Qwen alias rejects every reasoning_effort spelling while defaulting to
# thinking, so pin the route to a non-reasoning Flash Lite model instead of
# sending provider-specific thinking flags.
if [[ "$model" == "fast" ]]; then
  resolved_model="gemini-3.1-flash-lite"
fi

if [[ ! -f "$settings" ]]; then
  printf 'DeepSeek Harness settings not found: %s\n' "$settings" >&2
  exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

awk -v model="$resolved_model" -v agent_preset="$agent_preset" '
  /^agent-default-model:/ {
    print
    in_block = 1
    next
  }
  /^agent-presets:/ {
    print
    in_presets = 1
    next
  }
  in_block && /^[^[:space:]#]/ {
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
    next
  }
  in_presets && /^[^[:space:]#]/ {
    if (!replaced_preset) {
      print "  default: " agent_preset
    }
    in_presets = 0
    print
    next
  }
  in_presets && $1 == "default:" {
    print "  default: " agent_preset
    replaced_preset = 1
    next
  }
  { print }
  END {
    if (!replaced) {
      exit 10
    }
    if (in_presets && !replaced_preset) {
      print "  default: " agent_preset
    }
  }
' "$settings" > "$tmp"

mv "$tmp" "$settings"

# Keep the DSH web UI on the same low-latency memory agent that the voice
# assistant selects explicitly. The old memo-mem0 MCP preset remains available
# as an explicit fallback.
if ! grep -q '^agent-presets:' "$settings"; then
  printf '\nagent-presets:\n  default: %s\n' "$agent_preset" >> "$settings"
fi

# Keep one-shot replies short enough for the voice path without inheriting a
# coding-agent-sized output ceiling.
if [[ "$model" == "fast" ]]; then
  awk -v model_id="$resolved_model" -v max_tokens="$fast_max_tokens" '
    $0 ~ ("^[[:space:]]*- id:[[:space:]]*" model_id "[[:space:]]*$") {
      match($0, /^[[:space:]]*/)
      model_indent = substr($0, RSTART, RLENGTH)
      model_indent_length = RLENGTH
      print
      print model_indent "  maxTokens: " max_tokens
      in_model = 1
      next
    }
    in_model {
      match($0, /^[[:space:]]*/)
      lead_length = RLENGTH
      if ($0 ~ /^[[:space:]]*- / || ($0 !~ /^[[:space:]]*$/ && lead_length <= model_indent_length)) {
        in_model = 0
        print
        next
      }
      if ($1 == "maxTokens:") {
        next
      }
      print
      next
    }
    { print }
  ' "$settings" > "$tmp"

  mv "$tmp" "$settings"
  printf 'DeepSeek Harness fast routed to %s with maxTokens %s in %s\n' "$resolved_model" "$fast_max_tokens" "$settings" >&2
fi

printf 'DeepSeek Harness default model set to %s and default preset set to %s in %s\n' "$resolved_model" "$agent_preset" "$settings" >&2
