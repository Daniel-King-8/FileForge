"""文件上传：大小/配额/扩展名/libmagic 魔数三重校验。"""

import uuid
from pathlib import Path

from flask import Blueprint, g, jsonify, request

import alerts
import auth
import config
import formats
import models
import sniff
import utils

bp = Blueprint("upload", __name__, url_prefix="/api")

CHUNK = 1024 * 1024


def err(message: str, code: int = 400, **extra):
    return jsonify({"error": message, **extra}), code


def _save_stream(stream, dest: Path, limit: int) -> int:
    size = 0
    with open(dest, "wb") as fh:
        while True:
            chunk = stream.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError("too large")
            fh.write(chunk)
    return size


@bp.get("/formats")
def list_formats():
    category = request.args.get("category")
    result = {}
    for key, info in formats.CATEGORIES.items():
        if category and key != category:
            continue
        result[key] = {
            "label": info["label"],
            "tagline": info["tagline"],
            "exts": info["exts"],
            "targets": info["targets"],
            "options": info["options"],
            "targets_by_ext": {ext: formats.targets_for(key, ext) for ext in info["exts"]},
        }
    return jsonify({"categories": result})


@bp.post("/upload")
@auth.login_required
def upload():
    user = g.current_user
    cfg = config.cfg

    if alerts.disk_blocked():
        return err("服务器磁盘空间不足（已超过 95%），暂停接收新文件", 503)

    storage = request.files.get("file")
    if storage is None or not storage.filename:
        return err("没有收到文件")

    category = str(request.form.get("category") or "").strip().lower()
    if category not in formats.CATEGORIES:
        return err("未知的转换分类")

    src_name = utils.safe_filename(storage.filename)
    ext = utils.filename_ext(src_name)
    if not ext or not formats.ext_in_category(category, ext):
        return err(f"该分类不支持 .{ext or '?'} 格式", 415)

    if request.content_length and request.content_length > cfg.max_upload_bytes + 1024 * 1024:
        return err(f"文件超过单文件上限 {utils.human_size(cfg.max_upload_bytes)}", 413)

    remaining = user["quota_bytes"] - user["used_bytes"]
    if remaining <= 0:
        return err("配额已用完，请删除旧任务后再上传", 413)
    if request.content_length and request.content_length > remaining + 8192:
        return err(f"超出剩余配额（{utils.human_size(remaining)}）", 413)

    head = storage.stream.read(4096)
    storage.stream.seek(0)
    if not head:
        return err("文件为空")
    mime, kind = sniff.detect(head)
    if not sniff.kind_compatible(ext, kind):
        return err(
            f"文件真实类型为 {kind}，与扩展名 .{ext} 不符，已拒绝（防止伪装后缀）",
            415,
            detected=kind,
        )

    upload_id = uuid.uuid4().hex
    dest = cfg.uploads_dir / upload_id
    try:
        size = _save_stream(storage.stream, dest, cfg.max_upload_bytes)
    except ValueError:
        utils.remove_file(dest)
        return err(f"文件超过单文件上限 {utils.human_size(cfg.max_upload_bytes)}", 413)
    except OSError:
        utils.remove_file(dest)
        return err("保存文件失败，请稍后重试", 500)

    if size <= 0:
        utils.remove_file(dest)
        return err("文件为空")

    # 精确配额检查（Content-Length 可能被伪造）
    fresh = models.get_user(user["id"])
    if fresh is None or fresh["used_bytes"] + size > fresh["quota_bytes"]:
        utils.remove_file(dest)
        return err("超出用户总配额", 413)

    task = models.create_task(user["id"], src_name, upload_id, size, mime or "", category)
    models.add_used_bytes(user["id"], size)
    models.audit(user["id"], "upload", f"{task['id']} {src_name}", auth.client_ip(), auth.user_agent())

    return jsonify({
        "task": {
            "id": task["id"],
            "src_name": src_name,
            "src_size": size,
            "category": category,
            "status": task["status"],
        },
        "detected": {"mime": mime, "kind": kind},
        "targets": formats.targets_for(category, ext),
        "remaining_bytes": max(0, fresh["quota_bytes"] - fresh["used_bytes"] - size),
    }), 201
