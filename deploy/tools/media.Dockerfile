# ffmpeg / 7z / ImageMagick 工具镜像（固定 tag：fileforge/media-tools:1.0.0）
# 只用于一次性转换容器，network=none、只读挂载任务目录
FROM debian:12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        p7zip-full \
        imagemagick \
        fonts-dejavu-core \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
