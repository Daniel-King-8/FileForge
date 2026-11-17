"""Gunicorn 配置：gthread 模式兼顾普通请求与 SSE 长连接。"""

import os

bind = "0.0.0.0:8000"
worker_class = "gthread"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sus "%(a)s"'
