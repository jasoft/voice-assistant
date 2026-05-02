#!/usr/bin/env bash

set -e

PB_VERSION="0.22.21"
OS="darwin"
ARCH="arm64"

# If the system is not darwin arm64, try to detect
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
    if [[ $(uname -m) == "x86_64" ]]; then
        ARCH="amd64"
    fi
elif [[ $(uname -m) == "x86_64" ]]; then
    ARCH="amd64"
fi

PB_DIR="./.pocketbase"
PB_BIN="$PB_DIR/pocketbase"

mkdir -p "$PB_DIR"

if [ ! -f "$PB_BIN" ]; then
    echo "Downloading PocketBase v$PB_VERSION for $OS $ARCH..."
    ZIP_NAME="pocketbase_${PB_VERSION}_${OS}_${ARCH}.zip"
    curl -L -o "$PB_DIR/$ZIP_NAME" "https://github.com/pocketbase/pocketbase/releases/download/v${PB_VERSION}/${ZIP_NAME}"
    unzip -o "$PB_DIR/$ZIP_NAME" -d "$PB_DIR"
    rm "$PB_DIR/$ZIP_NAME"
    chmod +x "$PB_BIN"
fi

echo "Starting PocketBase on 0.0.0.0:18090..."
exec "$PB_BIN" serve --http="0.0.0.0:18090"
