#!/bin/zsh
# Run the Mac project-task executor in the foreground (logs to stderr).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
exec uv run python -m mac_executor.daemon "$@"
