#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_NAME="VoiceAssistantGUI"

find_release_binary() {
  local candidate
  for candidate in \
    "${PACKAGE_DIR}"/.build/out/Products/Release/"${APP_NAME}" \
    "${PACKAGE_DIR}"/.build/release/"${APP_NAME}" \
    "${PACKAGE_DIR}"/.build/*/release/"${APP_NAME}" \
    "${PACKAGE_DIR}"/.build/*/*/Release/"${APP_NAME}"; do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

latest_source_mtime() {
  python3 - "$PACKAGE_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
candidates = []
for relative in ("Package.swift", "Sources", "Tests"):
    path = root / relative
    if not path.exists():
        continue
    if path.is_file():
        candidates.append(path.stat().st_mtime)
        continue
    for item in path.rglob("*"):
        if item.is_file():
            candidates.append(item.stat().st_mtime)
print(max(candidates) if candidates else 0)
PY
}

needs_rebuild() {
  local binary_path="$1"
  local binary_mtime latest_source
  binary_mtime="$(stat -f '%m' "${binary_path}")"
  latest_source="$(latest_source_mtime)"
  [[ "${latest_source%.*}" -gt "${binary_mtime}" ]]
}

if BINARY_PATH="$(find_release_binary)"; then
  if ! needs_rebuild "${BINARY_PATH}"; then
    exec "${BINARY_PATH}" "$@"
  fi
fi

cd "${PACKAGE_DIR}"
swift build -c release

BINARY_PATH="$(find_release_binary)"
# 同步更新兼容路径，避免直接调用旧二进制路径失效
mkdir -p "${PACKAGE_DIR}/.build/arm64-apple-macosx/release"
ln -sf "${BINARY_PATH}" "${PACKAGE_DIR}/.build/arm64-apple-macosx/release/${APP_NAME}"

exec "${BINARY_PATH}" "$@"
