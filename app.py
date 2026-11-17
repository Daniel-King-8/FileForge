"""FileForge Web 应用：页面路由 + 蓝图注册。"""

import logging
import sys
from datetime import timedelta

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

import admin
import auth
import config
import db
import formats
import models
import tasks
import upload
import utils

cfg = config.cfg

STATUS_LABELS = {
    "pending": "等待转换",
    "running": "转换中",
    "done": "已完成",
    "failed": "失败",
    "expired": "已过期",
    "canceled": "已取消",
}

STATUS_CLASSES = {
    "pending": "warn",
    "running": "info",
    "done": "ok",
    "failed": "err",
    "expired": "muted",
    "canceled": "muted",
}


def create_app() -> Flask:
    cfg.ensure_dirs()

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=cfg.secret_key,
        MAX_CONTENT_LENGTH=cfg.max_upload_bytes + 2 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.secure_cookies,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=cfg.session_hours),
        TEMPLATES_AUTO_RELOAD=False,
        JSON_AS_ASCII=False,
    )

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s [app] %(message)s",
            stream=sys.stdout,
        )

    db.init_db()
    app.teardown_appcontext(db.close_db)

    app.register_blueprint(auth.bp)
    app.register_blueprint(upload.bp)
    app.register_blueprint(tasks.bp)
    app.register_blueprint(admin.bp)

    _register_filters(app)
    _register_pages(app)
    _register_errors(app)

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "app": cfg.app_name})

    return app


def _register_filters(app: Flask) -> None:
    app.jinja_env.filters["size"] = utils.human_size
    app.jinja_env.filters["dt"] = lambda value: utils.to_display(value, cfg.display_tz)
    app.jinja_env.filters["status_label"] = lambda value: STATUS_LABELS.get(value, value)
    app.jinja_env.filters["status_class"] = lambda value: STATUS_CLASSES.get(value, "muted")
    app.jinja_env.filters["cat_label"] = formats.category_label
    app.jinja_env.filters["ext"] = utils.filename_ext

    @app.context_processor
    def inject_globals():
        return {
            "user": auth.current_user(),
            "cfg": cfg,
            "categories": formats.CATEGORIES,
            "category_order": formats.CATEGORY_ORDER,
            "status_labels": STATUS_LABELS,
        }


def _register_pages(app: Flask) -> None:
    @app.get("/")
    def index():
        user = auth.current_user()
        recent, counts = [], {}
        if user:
            recent = models.list_tasks(user_id=user["id"], per=6)["items"]
            counts = models.task_counts(user["id"])
        return render_template("index.html", recent=recent, counts=counts)

    @app.get("/convert/<category>")
    def convert_page(category):
        if category not in formats.CATEGORIES:
            abort(404)
        info = formats.CATEGORIES[category]
        targets_by_ext = {ext: formats.targets_for(category, ext) for ext in info["exts"]}
        return render_template(
            "convert.html",
            category=category,
            info=info,
            targets_by_ext=targets_by_ext,
        )

    @app.get("/tasks")
    def tasks_page():
        user = auth.current_user()
        if not user:
            return redirect(url_for("login", next="/tasks"))
        scope = request.args.get("scope", "")
        owner = None if (scope == "all" and user["role"] == "admin") else user["id"]
        status = request.args.get("status") or None
        category = request.args.get("category") or None
        query = (request.args.get("q") or "").strip()
        page = request.args.get("page", 1)
        result = models.list_tasks(user_id=owner, status=status, category=category,
                                   query=query, page=page, per=15)
        return render_template(
            "tasks.html",
            result=result,
            counts=models.task_counts(owner),
            filters={"status": status or "", "category": category or "", "q": query,
                     "scope": scope},
            view_all=owner is None,
        )

    @app.get("/tasks/<task_id>")
    def task_detail(task_id):
        user = auth.current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        task = models.get_task(task_id)
        if task is None:
            abort(404)
        if task["user_id"] != user["id"] and user["role"] != "admin":
            abort(403)
        ext = utils.filename_ext(task["src_name"])
        return render_template(
            "task_detail.html",
            task=task,
            src_ext=ext,
            targets=formats.targets_for(task["category"], ext),
            options=utils.json_loads(task.get("options_json")),
        )

    @app.get("/share/<token>")
    def share_page(token):
        share = models.get_share(token)
        error = None
        task = None
        if share is None:
            error = "分享链接不存在或已被删除"
        else:
            expire = utils.parse_iso(share["expire_at"])
            if expire is not None and expire < utils.utcnow():
                error = "分享链接已过期"
            elif share["max_downloads"] and share["download_count"] >= share["max_downloads"]:
                error = "分享链接的下载次数已用完"
            else:
                task = models.get_task(share["task_id"])
                if not task or task["status"] != "done" or not task["result_path"]:
                    error = "文件已不可用或已过期"
        html = render_template("share.html", share=share, task=task, error=error, token=token)
        # 过期 / 失效的分享链接按验收要求返回 404
        return (html, 404) if error else html

    @app.get("/login")
    def login():
        if auth.current_user():
            return redirect(url_for("index"))
        return render_template("login.html", next=request.args.get("next", ""))

    @app.get("/register")
    def register():
        if auth.current_user():
            return redirect(url_for("index"))
        return render_template("register.html")

    @app.get("/profile")
    def profile():
        user = auth.current_user()
        if not user:
            return redirect(url_for("login", next="/profile"))
        counts = models.task_counts(user["id"])
        return render_template("profile.html", profile=user, counts=counts)

    @app.get("/admin")
    def admin_page():
        user = auth.current_user()
        if not user:
            return redirect(url_for("login", next="/admin"))
        if user["role"] != "admin":
            abort(403)
        return render_template("admin.html")


def _register_errors(app: Flask) -> None:
    def wants_json() -> bool:
        return request.path.startswith("/api/") or request.path.startswith("/download/")

    @app.errorhandler(403)
    def forbidden(_error):
        if wants_json():
            return jsonify({"error": "没有权限"}), 403
        return render_template("error.html", code=403, message="没有权限访问这个页面"), 403

    @app.errorhandler(404)
    def not_found(_error):
        if wants_json():
            return jsonify({"error": "资源不存在"}), 404
        return render_template("error.html", code=404, message="页面或文件不存在"), 404

    @app.errorhandler(413)
    def too_large(_error):
        message = f"上传文件超过 {utils.human_size(cfg.max_upload_bytes)} 限制"
        if wants_json():
            return jsonify({"error": message}), 413
        return render_template("error.html", code=413, message=message), 413

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("未处理异常: %s", error)
        if wants_json():
            return jsonify({"error": "服务器内部错误"}), 500
        return render_template("error.html", code=500, message="服务器内部错误"), 500


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
