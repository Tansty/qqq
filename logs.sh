#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
    sudo docker "$@"
  else
    echo "Docker 不可用，或当前用户没有 Docker 权限。" >&2
    exit 1
  fi
}

TAIL="${1:-120}"

echo "显示 qqq-advisor 最近 ${TAIL} 行日志..."
docker_cmd compose logs --tail="$TAIL" qqq-advisor
