#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

load_env_file() {
  [ -f "$PROJECT_DIR/.env" ] || return
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
  done < "$PROJECT_DIR/.env"
}

load_env_file

SCHEDULE="${QQQ_DAILY_CRON:-0 7 * * 2-6}"
MARKER="# qqq-advisor-daily"
JOB="$SCHEDULE cd $PROJECT_DIR && ./scripts/run_daily_docker.sh $MARKER"
TMP_FILE="$(mktemp)"

if crontab -l > "$TMP_FILE" 2>/dev/null; then
  sed -i.bak "/$MARKER/d" "$TMP_FILE"
else
  : > "$TMP_FILE"
fi

printf '%s\n' "$JOB" >> "$TMP_FILE"
crontab "$TMP_FILE"
rm -f "$TMP_FILE" "$TMP_FILE.bak"

echo "installed cron job:"
echo "$JOB"
