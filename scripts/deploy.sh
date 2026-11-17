#!/usr/bin/env bash
# 一键构建并启动 FileForge
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
    echo "错误：未找到 docker，请先安装 Docker Engine。" >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "错误：未找到 docker compose v2。" >&2
    exit 1
fi

[ -f .env ] || cp .env.example .env

get_env() {
    local key="$1"
    grep -E "^${key}=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}

set_env() {
    local key="$1" value="$2"
    if grep -qE "^${key}=" .env; then
        sed -i "s|^${key}=.*|${key}=${value}|" .env
    else
        printf '%s=%s\n' "$key" "$value" >> .env
    fi
}

if [ -z "$(get_env SECRET_KEY)" ]; then
    secret=""
    if command -v openssl >/dev/null 2>&1; then
        secret="$(openssl rand -hex 32)"
    fi
    if [ -z "$secret" ]; then
        secret="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    fi
    set_env SECRET_KEY "$secret"
    echo "已生成 SECRET_KEY"
fi

if [ -z "$(get_env ADMIN_PASSWORD)" ]; then
    admin_pw=""
    if command -v openssl >/dev/null 2>&1; then
        admin_pw="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-14)"
    fi
    if [ -z "$admin_pw" ]; then
        admin_pw="Admin$(date +%s)"
    fi
    set_env ADMIN_PASSWORD "$admin_pw"
    echo "已生成管理员密码：$admin_pw（写入 .env）"
fi

if [ -S /var/run/docker.sock ]; then
    gid="$(stat -c '%g' /var/run/docker.sock 2>/dev/null || echo 0)"
    set_env DOCKER_GID "$gid"
    echo "检测到 docker.sock 组 GID：$gid"
else
    echo "警告：未检测到 /var/run/docker.sock，worker 将无法启动转换容器。" >&2
fi

echo "==> 构建应用镜像"
docker compose build app

echo "==> 构建工具镜像（ffmpeg/7z 与 LibreOffice，首次可能较慢）"
docker compose --profile tools build

echo "==> 启动全部服务"
docker compose up -d

port="$(get_env HTTP_PORT)"
port="${port:-80}"
echo "==> 等待应用就绪"
ready=0
for _ in $(seq 1 45); do
    if docker compose exec -T app python -c \
        "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=2)" \
        >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    echo "警告：健康检查未通过，请执行 docker compose logs app 排查。" >&2
fi

docker compose ps
echo
echo "访问地址：http://<服务器IP>:${port}"
echo "管理员账号：$(get_env ADMIN_USERNAME) / $(get_env ADMIN_PASSWORD)"
echo "常用命令："
echo "  docker compose logs -f worker"
echo "  docker compose up -d --scale worker=2"
echo "  docker stats"
