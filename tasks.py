"""任务接口：列表、详情、转换、SSE、取消、重试、删除、下载、分享、批量操作。"""

import json
import re
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, Response, g, jsonify, request

import alerts
import auth
import config
import db
import formats
import models
import utils
from converters.base import log_tail

bp = Blueprint("tasks", __name__)

TERMINAL = {"done", "failed", "expired", "canceled"}
BATCH_RE = re.compile(r"^[0-9a-f]{32}\.zip$")
START_END_RE = re.compile(r"^\d{1,3}(?::\d{1,2}){0,2}(?:\.\d{1,3})?$")
RES_RE = re.compile(r"^\d{2,5}(?:x\d{2,5})?$")


def err(message: str, code: int = 400, **extra):
    return jsonify({"error": message, **extra}), code


def _owned_task(task_id: str):
    user = auth.current_user()
    task = models.get_task(task_id)
    if task is None:
        return None, err("任务不存在", 404)
    if task["user_id"] != user["id"] and user["role"] != "admin":
        return None, err("无权访问该任务", 403)
    return task, None


def _sanitize_options(category: str, raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    if category == "image":
        for key, low, high in (("quality", 1, 100), ("width", 0, 10000), ("height", 0, 10000)):
            if raw.get(key) not in (None, ""):
                try:
                    value = int(raw[key])
                except (TypeError, ValueError):
                    continue
                if low <= value <= high:
                    out[key] = value
    elif category == "media":
        for key in ("start", "end"):
            value = str(raw.get(key) or "").strip()
            if value and START_END_RE.match(value):
                out[key] = value
        value = str(raw.get("resolution") or "").strip()
        if value and RES_RE.match(value):
            out["resolution"] = value
    elif category == "archive":
        try:
            level = int(raw.get("level") or 6)
        except (TypeError, ValueError):
            level = 6
        out["level"] = max(1, min(9, level))
    return out


def task_json(task: dict) -> dict:
    data = dict(task)
    ext = utils.filename_ext(task["src_name"])
    data.pop("src_path", None)
    data["src_ext"] = ext
    data["available_targets"] = formats.targets_for(task["category"], ext)
    data["download_url"] = f"/download/{task['id']}" if task["status"] == "done" else None
    data["options"] = utils.json_loads(task.get("options_json"))
    return data


# ---------------------------------------------------------------- list / detail

@bp.get("/api/tasks")
@auth.login_required
def list_tasks():
    user = g.current_user
    scope = request.args.get("scope", "")
    owner = None if (scope == "all" and user["role"] == "admin") else user["id"]
    result = models.list_tasks(
        user_id=owner,
        status=request.args.get("status") or None,
        category=request.args.get("category") or None,
        query=(request.args.get("q") or "").strip(),
        page=request.args.get("page", 1),
        per=request.args.get("per", 15),
    )
    result["items"] = [task_json(t) for t in result["items"]]
    result["counts"] = models.task_counts(owner)
    return jsonify(result)


@bp.get("/api/tasks/<task_id>")
@auth.login_required
def get_task(task_id):
    task, error = _owned_task(task_id)
    if error:
        return error
    return jsonify({"task": task_json(task)})


@bp.get("/api/tasks/<task_id>/log")
@auth.login_required
def get_task_log(task_id):
    task, error = _owned_task(task_id)
    if error:
        return error
    text = log_tail(task_id, 20000)
    return Response(text or "（暂无日志）", mimetype="text/plain")


@bp.get("/api/tasks/<task_id>/preview")
@auth.login_required
def get_task_preview(task_id):
    """压缩包 list 任务的在线内容清单。"""
    task, error = _owned_task(task_id)
    if error:
        return error
    if task["status"] != "done" or task["target_format"] != "list" or not task["result_path"]:
        return err("该任务没有可预览的压缩包清单", 404)
    path = config.cfg.results_dir / task["result_path"]
    try:
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            return err("清单文件不存在或过大", 404)
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return err("清单文件解析失败", 500)
    return jsonify(data)


# ---------------------------------------------------------------- convert

@bp.post("/api/tasks/<task_id>/convert")
@auth.login_required
def convert(task_id):
    task, error = _owned_task(task_id)
    if error:
        return error
    if task["status"] not in ("pending", "failed", "canceled"):
        return err("当前状态不允许重新转换", 409)

    data = request.get_json(silent=True) or {}
    target = str(data.get("target_format") or "").strip().lower()
    ext = utils.filename_ext(task["src_name"])
    if not target or not formats.target_allowed(task["category"], ext, target):
        return err(f"不支持从 .{ext} 转换为 {target or '?'}")
    if not (config.cfg.uploads_dir / task["src_path"]).is_file():
        return err("源文件已过期，请重新上传", 410)
    if alerts.disk_blocked():
        return err("服务器磁盘空间不足，暂停新任务", 503)

    options = _sanitize_options(task["category"], data.get("options"))
    models.reset_for_convert(task_id, target, utils.json_dumps(options))
    models.audit(task["user_id"], "convert", f"{task_id} {target}", auth.client_ip(), auth.user_agent())
    return jsonify({"task": task_json(models.get_task(task_id))})


# ---------------------------------------------------------------- progress

@bp.get("/api/tasks/<task_id>/stream")
@auth.login_required
def stream(task_id):
    task, error = _owned_task(task_id)
    if error:
        return error

    def generate():
        conn = db.connect()
        started = time.time()
        try:
            while True:
                row = conn.execute(
                    "SELECT status, progress, error_msg, result_size FROM tasks WHERE id=?",
                    (task_id,),
                ).fetchone()
                if row is None:
                    yield "data: " + json.dumps({"deleted": True}) + "\n\n"
                    return
                yield "data: " + json.dumps(dict(row), ensure_ascii=False) + "\n\n"
                if row["status"] in TERMINAL:
                    return
                if time.time() - started > 1800:
                    return
                time.sleep(1)
        finally:
            conn.close()

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


# ---------------------------------------------------------------- actions

@bp.post("/api/tasks/<task_id>/cancel")
@auth.login_required
def cancel(task_id):
    task, error = _owned_task(task_id)
    if error:
        return error
    if task["status"] not in ("pending", "running"):
        return err("当前状态不能取消", 409)
    models.mark_canceled(task_id, task["user_id"])
    models.audit(task["user_id"], "cancel_task", task_id, auth.client_ip(), auth.user_agent())
    return jsonify({"ok": True})


@bp.post("/api/tasks/<task_id>/retry")
@auth.login_required
def retry(task_id):
    task, error = _owned_task(task_id)
    if error:
        return error
    if task["status"] not in ("failed", "canceled", "expired"):
        return err("只有失败/取消/过期的任务可以重试", 409)
    if not (config.cfg.uploads_dir / task["src_path"]).is_file():
        return err("源文件已被清理，请重新上传", 410)
    if alerts.disk_blocked():
        return err("服务器磁盘空间不足，暂停新任务", 503)
    models.reset_for_retry(task_id)
    models.audit(task["user_id"], "retry_task", task_id, auth.client_ip(), auth.user_agent())
    return jsonify({"task": task_json(models.get_task(task_id))})


@bp.delete("/api/tasks/<task_id>")
@auth.login_required
def delete(task_id):
    task, error = _owned_task(task_id)
    if error:
        return error
    if task["status"] == "running":
        models.mark_canceled(task_id, task["user_id"])

    utils.remove_file(config.cfg.uploads_dir / task["src_path"])
    if task["result_path"]:
        utils.remove_file(config.cfg.results_dir / task["result_path"])
    if task["status"] != "expired":
        models.add_used_bytes(task["user_id"], -int(task["src_size"]))
    models.delete_task_row(task_id)
    models.audit(task["user_id"], "delete_task", task_id, auth.client_ip(), auth.user_agent())
    return jsonify({"ok": True})


# ---------------------------------------------------------------- batch

@bp.post("/api/tasks/batch/delete")
@auth.login_required
def batch_delete():
    user = g.current_user
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    if not isinstance(ids, list) or not ids:
        return err("请选择任务")
    deleted = 0
    for item in ids[:100]:
        task = models.get_task(str(item))
        if not task or (task["user_id"] != user["id"] and user["role"] != "admin"):
            continue
        if task["status"] == "running":
            models.mark_canceled(task["id"], task["user_id"])
        utils.remove_file(config.cfg.uploads_dir / task["src_path"])
        if task["result_path"]:
            utils.remove_file(config.cfg.results_dir / task["result_path"])
        if task["status"] != "expired":
            models.add_used_bytes(task["user_id"], -int(task["src_size"]))
        models.delete_task_row(task["id"])
        deleted += 1
    models.audit(user["id"], "batch_delete", f"{deleted} tasks", auth.client_ip(), auth.user_agent())
    return jsonify({"ok": True, "deleted": deleted})


@bp.post("/api/tasks/batch/download")
@auth.login_required
def batch_download():
    user = g.current_user
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    if not isinstance(ids, list) or not ids:
        return err("请选择任务")
    if len(ids) > config.cfg.max_batch_download:
        return err(f"一次最多打包 {config.cfg.max_batch_download} 个任务")
    tasks = [t for t in models.batch_get_tasks([str(i) for i in ids], user["id"], user["role"] == "admin")
             if t["status"] == "done" and t["result_path"]]
    tasks = [t for t in tasks if (config.cfg.results_dir / t["result_path"]).is_file()]
    if not tasks:
        return err("所选任务没有可下载的结果", 404)

    config.cfg.batch_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.zip"
    dest = config.cfg.batch_dir / name
    used: set[str] = set()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for task in tasks:
            source = config.cfg.results_dir / task["result_path"]
            base = Path(task["result_name"] or source.name).name
            if base.lower() in used:
                base = f"{task['id'][:8]}_{base}"
            used.add(base.lower())
            zf.write(source, arcname=base)
    models.audit(user["id"], "batch_download", f"{len(tasks)} tasks", auth.client_ip(), auth.user_agent())
    return jsonify({"url": f"/api/tasks/batch/file/{name}", "count": len(tasks)})


@bp.get("/api/tasks/batch/file/<name>")
@auth.login_required
def batch_file(name):
    if not BATCH_RE.match(name):
        return err("文件名不合法", 400)
    path = config.cfg.batch_dir / name
    if not path.is_file():
        return err("打包文件已过期，请重新下载", 404)
    return _xaccel(f"_tmp/{name}", "fileforge-batch.zip", "application/zip")


# ---------------------------------------------------------------- download / share

def _content_disposition(filename: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9_.\- ]+", "_", filename) or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _xaccel(relative: str, filename: str, mime: str) -> Response:
    response = Response("", 200)
    response.headers["X-Accel-Redirect"] = f"/_files/{relative}"
    response.headers["Content-Type"] = mime
    response.headers["Content-Disposition"] = _content_disposition(filename)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.get("/download/<task_id>")
@auth.login_required
def download(task_id):
    task, error = _owned_task(task_id)
    if error:
        return error
    if task["status"] != "done" or not task["result_path"]:
        return err("任务尚未完成", 409)
    if not (config.cfg.results_dir / task["result_path"]).is_file():
        return err("结果文件已过期", 404)
    models.audit(task["user_id"], "download", task_id, auth.client_ip(), auth.user_agent())
    ext = utils.filename_ext(task["result_name"] or "")
    mime = formats.target_mime(task["category"], ext) if ext else "application/octet-stream"
    return _xaccel(task["result_path"], task["result_name"] or "result", mime)


@bp.post("/api/tasks/<task_id>/share")
@auth.login_required
def create_share(task_id):
    task, error = _owned_task(task_id)
    if error:
        return error
    if task["status"] != "done":
        return err("只有已完成的任务可以分享", 409)

    data = request.get_json(silent=True) or {}
    try:
        hours = int(data.get("expire_hours") if data.get("expire_hours") is not None
                    else config.cfg.result_ttl_hours)
    except (TypeError, ValueError):
        hours = config.cfg.result_ttl_hours
    hours = max(0, min(168, hours))
    try:
        max_downloads = int(data.get("max_downloads")
                            if data.get("max_downloads") is not None
                            else config.cfg.share_max_downloads)
    except (TypeError, ValueError):
        max_downloads = config.cfg.share_max_downloads
    max_downloads = max(0, min(10000, max_downloads))

    expire_at = utils.iso_after_hours(hours) if hours else None
    share = models.create_share(task_id, expire_at, max_downloads)
    models.audit(task["user_id"], "create_share", f"{task_id} {share['token']}",
                 auth.client_ip(), auth.user_agent())
    return jsonify({
        "token": share["token"],
        "url": f"/share/{share['token']}",
        "expire_at": expire_at,
        "max_downloads": max_downloads,
    })


@bp.get("/share/<token>/download")
def share_download(token):
    share = models.get_share(token)
    if share is None:
        return err("分享链接不存在", 404)
    expire = utils.parse_iso(share["expire_at"])
    if expire is not None and expire < utils.utcnow():
        return err("分享链接已过期", 404)
    if share["max_downloads"] and share["download_count"] >= share["max_downloads"]:
        return err("分享链接下载次数已用完", 404)
    task = models.get_task(share["task_id"])
    if not task or task["status"] != "done" or not task["result_path"]:
        return err("文件已不可用", 404)
    if not (config.cfg.results_dir / task["result_path"]).is_file():
        return err("文件已过期", 404)

    models.incr_share_download(token)
    models.audit(task["user_id"], "share_download", token, auth.client_ip(), auth.user_agent())
    ext = utils.filename_ext(task["result_name"] or "")
    mime = formats.target_mime(task["category"], ext) if ext else "application/octet-stream"
    return _xaccel(task["result_path"], task["result_name"] or "result", mime)
