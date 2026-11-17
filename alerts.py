"""磁盘水位检查与告警。"""

import shutil

import config
import models
import utils


def volume_usage(with_size: bool = False) -> list[dict]:
    cfg = config.cfg
    volumes = {
        "data": cfg.data_dir,
        "uploads": cfg.uploads_dir,
        "tmp": cfg.tmp_dir,
        "results": cfg.results_dir,
        "logs": cfg.logs_dir,
    }
    items = []
    for name, path in volumes.items():
        try:
            total, used, free = shutil.disk_usage(path)
            percent = round(used * 100 / total, 1) if total else 0.0
        except OSError:
            total = used = free = 0
            percent = 0.0
        item = {
            "name": name,
            "path": str(path),
            "total": total,
            "used": used,
            "free": free,
            "percent": percent,
        }
        if with_size:
            item["content"] = utils.dir_size(path)
        items.append(item)
    return items


def disk_percent() -> float:
    usage = volume_usage()
    return max((item["percent"] for item in usage), default=0.0)


def disk_blocked() -> bool:
    return disk_percent() >= config.cfg.disk_stop_pct


def check_disks() -> list[dict]:
    """检查所有卷，超阈值时写入告警（同一告警 1 小时内去重）。"""
    cfg = config.cfg
    usage = volume_usage()
    percent = max((item["percent"] for item in usage), default=0.0)
    since = utils.iso_after_minutes(-60)

    if percent >= cfg.disk_stop_pct:
        message = f"磁盘使用率 {percent}% 已超过 {cfg.disk_stop_pct}%，新任务将被拒绝"
        if not models.has_recent_alert(message, since):
            models.add_alert("critical", "cleanup", message)
    elif percent >= cfg.disk_warn_pct:
        message = f"磁盘使用率 {percent}% 已超过告警阈值 {cfg.disk_warn_pct}%"
        if not models.has_recent_alert(message, since):
            models.add_alert("warning", "cleanup", message)
    return usage
