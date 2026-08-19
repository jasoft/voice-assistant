#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

exec uv --directory "$project_root" run --no-sync python -m press_to_talk.memo_web "$@"
