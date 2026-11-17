"""管理员接口：系统状态、用户管理、告警、清理、审计。"""

import secrets

from flask import Blueprint, jsonify, request

import alerts
import auth
import config
import models
import security
import utils

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.get("/stats")
@auth.admin_required
def stats():
    cfg = config.cfg
    counts = models.task_counts()
    volumes = alerts.volume_usage(with_size=True)
    return jsonify({
        "overview": models.overview(),
        "tasks": counts,
        "queue": {
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "failed": counts.get("failed", 0),
            "done": counts.get("done", 0),
        },
        "workers": models.list_workers(),
        "containers": models.latest_container_stats(),
        "volumes": volumes,
        "disk_percent": max((v["percent"] for v in volumes), default=0),
        "limits": {
            "max_concurrent": cfg.max_concurrent,
            "disk_warn_pct": cfg.disk_warn_pct,
            "disk_stop_pct": cfg.disk_stop_pct,
            "max_upload": cfg.max_upload_bytes,
            "user_quota": cfg.user_quota_bytes,
        },
        "unacked_alerts": models.count_unacked_alerts(),
        "generated_at": utils.now_iso(),
    })


@bp.get("/users")
@auth.admin_required
def users():
    result = models.list_users(
        page=request.args.get("page", 1),
        per=request.args.get("per", 20),
        query=(request.args.get("q") or "").strip(),
    )
    for item in result["items"]:
        item.pop("password_hash", None)
    return jsonify(result)


@bp.post("/users/<int:user_id>/ban")
@auth.admin_required
def ban_user(user_id):
    me = auth.current_user()
    target = models.get_user(user_id)
    if not target:
        return auth.err("用户不存在", 404)
    if target["id"] == me["id"]:
        return auth.err("不能封禁自己", 400)
    if target["role"] == "admin":
        return auth.err("不能封禁管理员账号", 400)
    models.set_user_status(user_id, "banned")
    models.audit(me["id"], "admin_ban_user", str(user_id), auth.client_ip(), auth.user_agent())
    return jsonify({"ok": True})


@bp.post("/users/<int:user_id>/unban")
@auth.admin_required
def unban_user(user_id):
    me = auth.current_user()
    if not models.get_user(user_id):
        return auth.err("用户不存在", 404)
    models.set_user_status(user_id, "active")
    models.audit(me["id"], "admin_unban_user", str(user_id), auth.client_ip(), auth.user_agent())
    return jsonify({"ok": True})


@bp.post("/users/<int:user_id>/reset-password")
@auth.admin_required
def reset_password(user_id):
    me = auth.current_user()
    target = models.get_user(user_id)
    if not target:
        return auth.err("用户不存在", 404)
    if target["id"] == me["id"]:
        return auth.err("请通过个人中心修改自己的密码", 400)
    new_password = secrets.token_urlsafe(9)
    models.update_password(user_id, security.hash_password(new_password))
    models.audit(me["id"], "admin_reset_password", str(user_id), auth.client_ip(), auth.user_agent())
    return jsonify({"password": new_password})


@bp.get("/alerts")
@auth.admin_required
def list_alerts():
    unacked = request.args.get("unacked") == "1"
    return jsonify({"items": models.list_alerts(100, unacked_only=unacked)})


@bp.post("/alerts/<int:alert_id>/ack")
@auth.admin_required
def ack_alert(alert_id):
    me = auth.current_user()
    models.ack_alert(alert_id)
    models.audit(me["id"], "admin_ack_alert", str(alert_id), auth.client_ip(), auth.user_agent())
    return jsonify({"ok": True})


@bp.get("/tasks")
@auth.admin_required
def all_tasks():
    result = models.list_tasks(
        user_id=None,
        status=request.args.get("status") or None,
        category=request.args.get("category") or None,
        query=(request.args.get("q") or "").strip(),
        page=request.args.get("page", 1),
        per=request.args.get("per", 20),
    )
    return jsonify(result)


@bp.get("/audit")
@auth.admin_required
def audit_logs():
    return jsonify({"items": models.recent_audit(100)})


@bp.post("/cleanup")
@auth.admin_required
def run_cleanup():
    import cleanup

    me = auth.current_user()
    summary = cleanup.run_once()
    models.audit(me["id"], "admin_cleanup", str(summary), auth.client_ip(), auth.user_agent())
    return jsonify({"ok": True, "summary": summary})


@bp.post("/alerts/test")
@auth.admin_required
def test_alert():
    me = auth.current_user()
    models.add_alert("info", "admin", f"管理员 {me['username']} 触发的测试告警")
    return jsonify({"ok": True})
