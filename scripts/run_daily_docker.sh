#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

load_env_file() {
  [ -f ".env" ] || return
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ""|\#*) continue ;;
      *=*)
        key="${line%%=*}"
        value="${line#*=}"
        case "$key" in
          *[!A-Za-z0-9_]*|"") continue ;;
        esac
        case "$value" in
          \"*\") value="${value#\"}"; value="${value%\"}" ;;
          \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        export "$key=$value"
        ;;
    esac
  done < ".env"
}

load_env_file

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
