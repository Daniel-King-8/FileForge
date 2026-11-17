#!/usr/bin/env bash
# 部署前环境自检
set -u
cd "$(dirname "$0")/.."

echo "== Docker =="
docker --version 2>/dev/null || echo "未安装 docker"
echo
echo "== Docker Compose =="
docker compose version 2>/dev/null || echo "未安装 docker compose v2"
echo
echo "== docker.sock =="
if [ -S /var/run/docker.sock ]; then
    ls -l /var/run/docker.sock
    echo "建议写入 .env：DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)"
else
    echo "未找到 /var/run/docker.sock"
fi
echo
echo "== 端口占用（80） =="
if command -v ss >/dev/null 2>&1; then
    ss -ltnp 2>/dev/null | grep ':80 ' || echo "80 端口空闲"
elif command -v netstat >/dev/null 2>&1; then
    netstat -ltnp 2>/dev/null | grep ':80 ' || echo "80 端口空闲"
else
    echo "未找到 ss/netstat"
fi
echo
echo "== 磁盘 =="
df -h /var/lib/docker 2>/dev/null || df -h /
echo
echo "== .env =="
if [ -f .env ]; then
    grep -E '^(SECRET_KEY|ADMIN_PASSWORD|DOCKER_GID|HTTP_PORT)=' .env | sed 's/=.*/=<已设置>/' || true
else
    echo ".env 不存在，deploy.sh 会自动创建"
fi
