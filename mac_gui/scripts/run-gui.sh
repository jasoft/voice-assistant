#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_NAME="VoiceAssistantGUI"

find_release_binary() {
  local candidate
  for candidate in "${PACKAGE_DIR}"/.build/*/release/"${APP_NAME}"; do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

if BINARY_PATH="$(find_release_binary)"; then
  exec "${BINARY_PATH}"
fi

cd "${PACKAGE_DIR}"
swift build -c release

BINARY_PATH="$(find_release_binary)"
exec "${BINARY_PATH}"
