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

# The repository may be built from macOS, while this image runs Linux. Never
# reuse a host PocketBase binary when it cannot execute in the current image.
if [ -f "$PB_BIN" ] && ! "$PB_BIN" --version >/dev/null 2>&1; then
    echo "Existing PocketBase binary is not executable on this platform; downloading a compatible copy..."
    rm -f "$PB_BIN"
fi

if [ ! -f "$PB_BIN" ]; then
    echo "Downloading PocketBase v$PB_VERSION for $OS $ARCH..."
    ZIP_NAME="pocketbase_${PB_VERSION}_${OS}_${ARCH}.zip"
    curl -L -o "$PB_DIR/$ZIP_NAME" "https://github.com/pocketbase/pocketbase/releases/download/v${PB_VERSION}/${ZIP_NAME}"
    unzip -o "$PB_DIR/$ZIP_NAME" -d "$PB_DIR"
    rm "$PB_DIR/$ZIP_NAME"
    chmod +x "$PB_BIN"
fi

PB_DATA_DIR="$PB_DIR/pb_data"
if [ -d "/app/data" ]; then
    # Docker 环境下使用挂载的卷
    PB_DATA_DIR="/app/data/pb_data"
fi

echo "Starting PocketBase on 0.0.0.0:18090 using data dir $PB_DATA_DIR..."
exec "$PB_BIN" serve --http="0.0.0.0:18090" --dir="$PB_DATA_DIR"
