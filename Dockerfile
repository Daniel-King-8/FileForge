# syntax=docker/dockerfile:1.7

# 从官方 docker CLI 镜像取静态二进制（走 Docker Hub / 镜像加速器，
# 避免额外依赖 download.docker.com）。
FROM docker:27.3.1-cli AS dockercli

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates libmagic1 tini tzdata; \
    rm -rf /var/lib/apt/lists/*

# app / worker / cleanup 共用同一镜像，仅启动命令不同。
# worker 需要 docker CLI 来启动一次性工具容器。
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN set -eux; \
    useradd --uid 1000 --create-home --shell /usr/sbin/nologin fileforge; \
    mkdir -p /srv/data /srv/uploads /srv/tmp /srv/results /srv/logs; \
    chown -R fileforge:fileforge /srv /app

USER fileforge

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
