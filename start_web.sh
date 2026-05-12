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

python3 -m py_compile qqq_advisor.py web_server.py qqq_agent.py

HOST="${QQQ_ADVISOR_HOST:-127.0.0.1}"
PORT="${QQQ_ADVISOR_PORT:-8765}"

echo "QQQ Advisor 网页端启动中..."
echo "地址: http://${HOST}:${PORT}"
echo "按 Ctrl+C 停止服务"

exec python3 web_server.py --host "$HOST" --port "$PORT"
