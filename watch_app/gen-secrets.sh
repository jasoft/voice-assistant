#!/bin/bash
# 从仓库根 .env 生成 WatchApp/Sources/Secrets.swift（默认服务器与 API Key）。
# 生成物已被 gitignore，勿提交。可用环境变量覆盖：
#   WATCH_SERVER_URL  手表默认服务器地址（默认公网反代地址）
#   ENV_FILE          .env 路径（默认仓库根 ../.env）
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="${ENV_FILE:-$(pwd)/../.env}"
server_url="${WATCH_SERVER_URL:-https://va.soj.myds.me:1443}"
api_key=""

if [ -f "$ENV_FILE" ]; then
  key_from_env=$(grep -m1 '^PTT_API_KEY=' "$ENV_FILE" | cut -d= -f2- | tr -d '\r' || true)
  api_key="${key_from_env:-}"
fi

cat > WatchApp/Sources/Secrets.swift <<EOF
// 由 gen-secrets.sh 生成，勿提交到 git（watch_app/.gitignore 已忽略）。
enum Secrets {
    static let defaultServerURL = "${server_url}"
    static let defaultAPIKey = "${api_key}"
}
EOF
echo "Secrets.swift generated (server=${server_url}, api_key=$([ -n "$api_key" ] && echo '<set>' || echo '<empty>'))"
