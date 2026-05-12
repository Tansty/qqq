#!/usr/bin/env bash
set -euo pipefail

MARKER="# qqq-advisor-daily"
TMP_FILE="$(mktemp)"

if crontab -l > "$TMP_FILE" 2>/dev/null; then
  sed -i.bak "/$MARKER/d" "$TMP_FILE"
  crontab "$TMP_FILE"
fi

rm -f "$TMP_FILE" "$TMP_FILE.bak"
echo "removed qqq advisor daily cron job"
