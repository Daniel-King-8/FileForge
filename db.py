"""SQLite 连接管理。

- WAL 模式 + busy_timeout=5000，允许 app / worker / cleanup 多进程并发；
- Web 请求内复用 Flask g 上的连接，脚本进程每次新建连接。
"""

import logging
import secrets
import sqlite3
from contextlib import contextmanager

from flask import g, has_app_context

import config
import security
import utils

log = logging.getLogger("fileforge.db")

SCHEMA_PATH = config.cfg.base_dir / "deploy" / "init.sql"


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(path or config.cfg.db_path),
        timeout=10,
        isolation_level=None,  # 自动提交，需要事务时显式 BEGIN
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def session():
    """在 Web 请求内复用连接，在 worker/cleanup 中临时新建。"""
    if has_app_context():
        yield get_db()
    else:
        conn = connect()
        try:
            yield conn
        finally:
            conn.close()


def init_db() -> None:
    config.cfg.ensure_dirs()
    with session() as conn:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema)
    _bootstrap_admin()


def _bootstrap_admin() -> None:
    with session() as conn:
        row = conn.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
        if row:
            return
        password = config.cfg.admin_password
        generated = False
        if not password:
            password = secrets.token_urlsafe(12)
            generated = True
        now = utils.now_iso()
        try:
            conn.execute(
                """INSERT INTO users (username, email, password_hash, role, quota_bytes,
                                      used_bytes, status, created_at)
                   VALUES (?, ?, ?, 'admin', ?, 0, 'active', ?)""",
                (
                    config.cfg.admin_username,
                    config.cfg.admin_email,
                    security.hash_password(password),
                    config.cfg.user_quota_bytes,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            log.warning("管理员初始化跳过：用户名或邮箱已被占用")
            return
        if generated:
            log.warning(
                "已创建管理员账号 %s，随机密码：%s （请尽快登录修改）",
                config.cfg.admin_username,
                password,
            )
        else:
            log.info("已创建管理员账号 %s", config.cfg.admin_username)
