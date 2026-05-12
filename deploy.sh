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

linux_id() {
  if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    echo "${ID:-}"
  fi
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

sudo_docker_usable() {
  need_command sudo && sudo -n docker info >/dev/null 2>&1
}

docker_cmd() {
  if docker_usable; then
    docker "$@"
  elif sudo_docker_usable; then
    sudo docker "$@"
  else
    echo "Docker 服务未正常可用，或当前用户没有 Docker 权限。" >&2
    exit 1
  fi
}

install_docker_with_package_manager() {
  if need_command dnf; then
    run_as_root dnf install -y docker
    run_as_root dnf install -y docker-compose-plugin || true
  elif need_command yum; then
    run_as_root yum install -y docker
    run_as_root yum install -y docker-compose-plugin || true
  elif need_command apt-get; then
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    run_as_root sh /tmp/get-docker.sh
  else
    echo "无法自动安装 Docker，请先手动安装后重试。" >&2
    exit 1
  fi
}

install_compose_plugin_if_missing() {
  if docker_cmd compose version >/dev/null 2>&1; then
    return
  fi

  if need_command dnf; then
    run_as_root dnf install -y docker-compose-plugin || true
  elif need_command yum; then
    run_as_root yum install -y docker-compose-plugin || true
  elif need_command apt-get; then
    run_as_root apt-get update
    run_as_root apt-get install -y docker-compose-plugin || true
  fi

  if docker_cmd compose version >/dev/null 2>&1; then
    return
  fi

  install_package_if_missing curl curl
  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
    *)
      echo "不支持自动安装 Docker Compose 插件的架构: $arch" >&2
      exit 1
      ;;
  esac

  info "安装 Docker Compose 插件"
  run_as_root mkdir -p /usr/local/lib/docker/cli-plugins
  run_as_root curl -fSL \
    "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${arch}" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  run_as_root chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

  if ! docker_cmd compose version >/dev/null 2>&1; then
    echo "Docker Compose 插件安装失败，请检查网络或手动安装 docker compose。" >&2
    exit 1
  fi
}

buildx_version_ok() {
  local version
  version="$(docker_cmd buildx version 2>/dev/null | sed -n 's/.* v\([0-9][0-9.]*\).*/\1/p' | head -n 1)"
  [ -n "$version" ] || return 1
  python3 - "$version" <<'PY' >/dev/null 2>&1
import sys

version = tuple(int(part) for part in sys.argv[1].split(".")[:3])
required = (0, 17, 0)
while len(version) < 3:
    version += (0,)
raise SystemExit(0 if version >= required else 1)
PY
}

install_buildx_plugin_if_needed() {
  if buildx_version_ok; then
    return
  fi

  install_package_if_missing curl curl
  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *)
      echo "不支持自动安装 Docker Buildx 插件的架构: $arch" >&2
      exit 1
      ;;
  esac

  info "安装新版 Docker Buildx 插件"
  run_as_root mkdir -p /usr/local/lib/docker/cli-plugins
  run_as_root curl -fSL \
    "https://github.com/docker/buildx/releases/latest/download/buildx-v0.21.2.linux-${arch}" \
    -o /usr/local/lib/docker/cli-plugins/docker-buildx
  run_as_root chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx

  if ! buildx_version_ok; then
    echo "Docker Buildx 插件安装失败，请检查网络或手动安装 buildx >= 0.17.0。" >&2
    exit 1
  fi
}

install_docker_if_missing() {
  if need_command docker && docker_usable; then
    install_compose_plugin_if_missing
    install_buildx_plugin_if_needed
    return
  fi

  if [ "$(uname -s)" != "Linux" ]; then
    echo "当前系统不是 Linux，无法自动安装 Docker。请手动安装 Docker Desktop 后重试。" >&2
    exit 1
  fi

  install_package_if_missing curl curl

  if ! need_command docker; then
    info "安装 Docker"
    if [ "$(linux_id)" = "amzn" ]; then
      install_docker_with_package_manager
    else
      curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
      run_as_root sh /tmp/get-docker.sh
    fi
  fi

  if need_command systemctl; then
    run_as_root systemctl enable --now docker || true
  else
    run_as_root service docker start || true
  fi

  if [ "$(id -u)" -ne 0 ] && getent group docker >/dev/null 2>&1; then
    run_as_root usermod -aG docker "$USER" || true
  fi

  if ! docker_usable && ! sudo_docker_usable; then
    echo "Docker 服务未正常可用，请检查 docker service 状态。" >&2
    exit 1
  fi

  install_compose_plugin_if_missing
  install_buildx_plugin_if_needed
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
  if ! docker_cmd compose up -d --build; then
    info "compose build 失败，尝试使用 docker build 兜底"
    docker_cmd build -t qqq-qqq-advisor .
    docker_cmd compose up -d --no-build
  fi
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
