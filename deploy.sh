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

PORT="${QQQ_ADVISOR_PORT:-8765}"
USERNAME="${QQQ_ADVISOR_USERNAME:-advisor}"
CRON_SCHEDULE="${QQQ_DAILY_CRON:-0 7 * * 2-6}"

info() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

need_command() {
  command -v "$1" >/dev/null 2>&1
}

run_as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif need_command sudo; then
    sudo "$@"
  else
    echo "需要 root 权限执行: $*" >&2
    echo "请用 root 登录，或先安装 sudo 后再运行 ./deploy.sh" >&2
    exit 1
  fi
}

install_package_if_missing() {
  local command_name="$1"
  local package_name="$2"
  if need_command "$command_name"; then
    return
  fi
  if need_command apt-get; then
    run_as_root apt-get update
    run_as_root apt-get install -y "$package_name"
  elif need_command dnf; then
    run_as_root dnf install -y "$package_name"
  elif need_command yum; then
    run_as_root yum install -y "$package_name"
  else
    echo "无法自动安装 $package_name，请先手动安装后重试。" >&2
    exit 1
  fi
}

docker_usable() {
  docker info >/dev/null 2>&1
}

install_docker_if_missing() {
  if need_command docker && docker_usable; then
    return
  fi

  if [ "$(uname -s)" != "Linux" ]; then
    echo "当前系统不是 Linux，无法自动安装 Docker。请手动安装 Docker Desktop 后重试。" >&2
    exit 1
  fi

  install_package_if_missing curl curl

  if ! need_command docker; then
    info "安装 Docker"
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    run_as_root sh /tmp/get-docker.sh
  fi

  if need_command systemctl; then
    run_as_root systemctl enable --now docker || true
  else
    run_as_root service docker start || true
  fi

  if ! docker_usable; then
    if [ "$(id -u)" -ne 0 ]; then
      echo "Docker 已安装，但当前用户还不能直接访问 Docker。" >&2
      echo "请执行下面命令后重新登录，再运行 ./deploy.sh：" >&2
      echo "  sudo usermod -aG docker $USER" >&2
      exit 1
    fi
    echo "Docker 服务未正常可用，请检查 docker service 状态。" >&2
    exit 1
  fi
}

install_cron_if_missing() {
  if need_command crontab; then
    return
  fi
  if need_command apt-get; then
    run_as_root apt-get update
    run_as_root apt-get install -y cron
    run_as_root systemctl enable --now cron || true
  elif need_command dnf; then
    run_as_root dnf install -y cronie
    run_as_root systemctl enable --now crond || true
  elif need_command yum; then
    run_as_root yum install -y cronie
    run_as_root systemctl enable --now crond || true
  else
    echo "无法自动安装 cron，请先手动安装后重试。" >&2
    exit 1
  fi
}

random_password() {
  if need_command openssl; then
    openssl rand -hex 24 | tr -d '\n'
  else
    LC_ALL=C tr -dc 'A-Fa-f0-9' < /dev/urandom | head -c 48
  fi
}

write_env_if_needed() {
  local password="${QQQ_ADVISOR_PASSWORD:-}"
  if [ -f ".env" ]; then
    if ! grep -q '^QQQ_ADVISOR_PASSWORD=' ".env"; then
      password="$(random_password)"
      printf '\nQQQ_ADVISOR_PASSWORD=%s\n' "$password" >> ".env"
      export QQQ_ADVISOR_PASSWORD="$password"
      info "已写入 .env 网页登录密码"
      echo "网页登录用户名: ${USERNAME}"
      echo "网页登录密码: ${password}"
    elif grep -Eq '^QQQ_ADVISOR_PASSWORD=$|^QQQ_ADVISOR_PASSWORD=replace-with-a-long-random-password$' ".env"; then
      password="$(random_password)"
      sed -i.bak "s/^QQQ_ADVISOR_PASSWORD=.*/QQQ_ADVISOR_PASSWORD=$password/" ".env"
      rm -f .env.bak
      export QQQ_ADVISOR_PASSWORD="$password"
      info "已替换 .env 里的默认网页登录密码"
      echo "网页登录用户名: ${USERNAME}"
      echo "网页登录密码: ${password}"
    fi
    return
  fi

  if [ -z "$password" ]; then
    password="$(random_password)"
  fi

  cat > ".env" <<EOF
QWEN_API_KEY=${QWEN_API_KEY:-}
QWEN_MODEL=${QWEN_MODEL:-qwen3.6-plus-2026-04-02}
QWEN_BASE_URL=${QWEN_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions}

QQQ_ADVISOR_PORT=${PORT}
QQQ_ADVISOR_USERNAME=${USERNAME}
QQQ_ADVISOR_PASSWORD=${password}
QQQ_DAILY_CRON=${CRON_SCHEDULE}
EOF
  export QQQ_ADVISOR_PASSWORD="$password"

  info "已生成 .env"
  echo "网页登录用户名: ${USERNAME}"
  echo "网页登录密码: ${password}"
}

compose_up() {
  info "构建并启动 QQQ Advisor"
  docker compose up -d --build
}

install_daily_job() {
  info "安装每日定时任务"
  QQQ_DAILY_CRON="$CRON_SCHEDULE" ./scripts/install_cron_daily.sh
}

main() {
  info "开始一键部署 QQQ Advisor"
  install_docker_if_missing
  install_cron_if_missing
  write_env_if_needed
  ./scripts/prepare_storage.sh
  compose_up
  install_daily_job

  info "部署完成"
  echo "访问地址: http://服务器IP:${PORT}"
  echo "默认定时: ${CRON_SCHEDULE}"
  echo "日志目录: storage/logs/"
  echo "数据目录: storage/"
}

main "$@"
