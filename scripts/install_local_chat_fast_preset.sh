#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${HOME}/.dsh/.agent-presets/chat-fast"
mkdir -p "$(dirname "$target")"
if [ -e "$target" ] || [ -L "$target" ]; then
  rm -rf "$target"
fi
cp -R "$root/config/deepseek-harness/agent-presets/chat-fast" "$target"
echo "Installed chat-fast preset: $target"
