#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

mkdir -p storage/logs
LOG_FILE="storage/logs/daily-$(date +%Y%m%d).log"

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

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %z') daily job start ====="
  docker_cmd compose exec -T qqq-advisor python3 qqq_agent.py daily --config "${QQQ_ADVISOR_CONFIG:-/app/storage/config.json}"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %z') daily job done ====="
} >> "$LOG_FILE" 2>&1
