#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

python3 -m py_compile qqq_advisor.py qqq_agent.py

if [ "${QQQ_AGENT_NO_QWEN:-0}" = "1" ]; then
  exec python3 qqq_agent.py daily --no-qwen
fi

exec python3 qqq_agent.py daily
