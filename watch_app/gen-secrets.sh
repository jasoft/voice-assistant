#!/bin/bash
# 从仓库根 .env 生成 WatchApp/Sources/Secrets.swift（默认服务器与 API Key）。
# 生成物已被 gitignore，勿提交。可用环境变量覆盖：
#   WATCH_SERVER_URL  手表默认服务器地址（默认公网反代地址）
#   ENV_FILE          .env 路径（默认仓库根 ../.env）
set -euo pipefail
cd "$(dirname "$0")"

server_url="${WATCH_SERVER_URL:-https://va.soj.myds.me:1443}"
api_key="${PTT_API_KEY:-}"

# 寻找 .env 文件的候选路径
CANDIDATES=(
  "${ENV_FILE:-}"
  "$(pwd)/../.env"
  "$(git rev-parse --show-toplevel 2>/dev/null || true)/.env"
  "$(cd "$(git rev-parse --git-common-dir 2>/dev/null || true)/.." 2>/dev/null && pwd)/.env"
)

for f in "${CANDIDATES[@]}"; do
  if [ -n "$f" ] && [ -f "$f" ]; then
    key_from_env=$(grep -m1 '^PTT_API_KEY=' "$f" | cut -d= -f2- | tr -d '\r"' || true)
    if [ -n "$key_from_env" ]; then
      api_key="$key_from_env"
      echo "==> 从 $f 提取到 PTT_API_KEY"
      break
    fi
  fi
done

# 如果仍未找到，回退到已知默认值，避免手表构建出无凭证安装包
if [ -z "$api_key" ]; then
  echo "⚠️  未在 .env 中找到 PTT_API_KEY，回退到默认 soj-default-token"
  api_key="soj-default-token"
fi

cat > WatchApp/Sources/Secrets.swift <<EOF
// 由 gen-secrets.sh 生成，勿提交到 git（watch_app/.gitignore 已忽略）。
enum Secrets {
    static let defaultServerURL = "${server_url}"
    static let defaultAPIKey = "${api_key}"
}
EOF
echo "Secrets.swift generated (server=${server_url}, api_key=<set>)"
