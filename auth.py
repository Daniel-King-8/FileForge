"""用户系统：注册、登录、登出、个人资料、改密。"""

import re
import sqlite3
from functools import wraps

from flask import Blueprint, g, jsonify, request, session

import models
import security
import utils

bp = Blueprint("auth", __name__, url_prefix="/api")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def client_ip() -> str:
    forwarded = request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.remote_addr) or "unknown"


def user_agent() -> str:
    return (request.headers.get("User-Agent") or "")[:300]


def err(message: str, code: int = 400, **extra):
    return jsonify({"error": message, **extra}), code


def current_user() -> dict | None:
    if "current_user" in g:
        return g.current_user
    uid = session.get("uid")
    user = models.get_user(uid) if uid else None
    if user and user.get("status") != "active":
        session.clear()
        user = None
    g.current_user = user
    return user


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return err("请先登录", 401)
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return err("请先登录", 401)
        if user.get("role") != "admin":
            return err("需要管理员权限", 403)
        return view(*args, **kwargs)

    return wrapper


def public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "quota_bytes": user["quota_bytes"],
        "used_bytes": user["used_bytes"],
        "status": user["status"],
        "created_at": user["created_at"],
    }


def _lock_seconds(username: str, ip: str) -> int:
    """返回剩余锁定秒数，0 表示未锁定。"""
    import config

    cfg = config.cfg
    since = utils.iso_after_minutes(-cfg.login_window_minutes)
    failures = models.count_login_failures(username, ip, since)
    if failures < cfg.login_max_failures:
        return 0
    last = utils.parse_iso(models.last_login_failure(username, ip))
    if last is None:
        return 0
    elapsed = (utils.utcnow() - last).total_seconds()
    remain = cfg.login_lock_minutes * 60 - elapsed
    return max(0, int(remain))


@bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    email = str(data.get("email") or "").strip()
    password = str(data.get("password") or "")

    if not USERNAME_RE.match(username):
        return err("用户名需为 3-32 位字母、数字、下划线或短横线")
    if not EMAIL_RE.match(email) or len(email) > 120:
        return err("邮箱格式不正确")
    problems = security.password_problems(password)
    if problems:
        return err("；".join(problems))
    if models.get_user_by_username(username):
        return err("用户名已被占用", 409)
    if models.get_user_by_email(email):
        return err("邮箱已被注册", 409)

    try:
        user = models.create_user(username, email, security.hash_password(password))
    except sqlite3.IntegrityError:
        return err("用户名或邮箱已被占用", 409)

    session.clear()
    session["uid"] = user["id"]
    session.permanent = True
    models.audit(user["id"], "register", username, client_ip(), user_agent())
    return jsonify({"user": public_user(user)}), 201


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    ip = client_ip()

    if not username or not password:
        return err("请输入用户名和密码")

    remain = _lock_seconds(username, ip)
    if remain > 0:
        return err(f"失败次数过多，请 {remain // 60 + 1} 分钟后再试", 429, retry_after=remain)

    user = models.get_user_by_username(username)
    if user is None and "@" in username:
        user = models.get_user_by_email(username)
    if user is None or not security.verify_password(password, user["password_hash"]):
        models.audit(user["id"] if user else None, "login_failed", username, ip, user_agent())
        return err("用户名或密码错误", 401)
    if user["status"] != "active":
        models.audit(user["id"], "login_banned", username, ip, user_agent())
        return err("账号已被封禁，请联系管理员", 403)

    session.clear()
    session["uid"] = user["id"]
    session.permanent = True
    models.audit(user["id"], "login", username, ip, user_agent())
    return jsonify({"user": public_user(user)})


@bp.post("/logout")
def logout():
    user = current_user()
    if user:
        models.audit(user["id"], "logout", user["username"], client_ip(), user_agent())
    session.clear()
    return jsonify({"ok": True})


@bp.get("/me")
def me():
    user = current_user()
    if not user:
        return jsonify({"user": None})
    counts = models.task_counts(user["id"])
    return jsonify({
        "user": public_user(user),
        "is_admin": user["role"] == "admin",
        "task_counts": counts,
        "unacked_alerts": models.count_unacked_alerts() if user["role"] == "admin" else 0,
    })


@bp.post("/profile/password")
@login_required
def change_password():
    user = current_user()
    data = request.get_json(silent=True) or {}
    old = str(data.get("old_password") or "")
    new = str(data.get("new_password") or "")
    if not security.verify_password(old, user["password_hash"]):
        return err("当前密码不正确", 403)
    problems = security.password_problems(new)
    if problems:
        return err("；".join(problems))
    if old == new:
        return err("新密码不能与当前密码相同")
    models.update_password(user["id"], security.hash_password(new))
    models.audit(user["id"], "change_password", user["username"], client_ip(), user_agent())
    session.clear()
    return jsonify({"ok": True, "relogin": True})
