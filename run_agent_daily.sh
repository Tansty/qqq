#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

python3 -m py_compile qqq_advisor.py qqq_agent.py

if [ "${QQQ_AGENT_NO_QWEN:-0}" = "1" ]; then
  exec python3 qqq_agent.py daily --no-qwen
fi

exec python3 qqq_agent.py daily
