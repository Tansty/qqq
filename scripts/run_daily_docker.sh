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

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %z') daily job start ====="
  docker compose exec -T qqq-advisor python3 qqq_agent.py daily --config "${QQQ_ADVISOR_CONFIG:-/app/storage/config.json}"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %z') daily job done ====="
} >> "$LOG_FILE" 2>&1
