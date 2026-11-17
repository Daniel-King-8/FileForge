"""定时清理：过期任务/文件、失联任务、过期分享、审计与告警、孤儿文件、磁盘告警。

容器内以循环方式运行：
    python cleanup.py            # 每 CLEANUP_INTERVAL 秒执行一次
    CLEANUP_ONCE=1 python cleanup.py   # 只跑一轮（调试/管理后台触发）
"""

import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import alerts
import config
import db
import models
import utils

cfg = config.cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [cleanup] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("fileforge.cleanup")

AUDIT_RETENTION_DAYS = 30
ORPHAN_GRACE_HOURS = 25
TMP_GRACE_HOURS = 2


def _kill_task_container(task_id: str) -> None:
    try:
        found = subprocess.run(
            [cfg.docker_bin, "ps", "-q", "--filter", f"name=ff-{task_id[:8]}"],
            capture_output=True, text=True, timeout=15,
        )
        ids = [line.strip() for line in found.stdout.splitlines() if line.strip()]
        if ids:
            subprocess.run([cfg.docker_bin, "kill", *ids],
                           capture_output=True, text=True, timeout=20)
            log.info("已终止失联任务的转换容器：%s", ", ".join(ids))
    except (OSError, subprocess.TimeoutExpired):
        pass


def _remove_task_files(task: dict) -> None:
    utils.remove_file(cfg.uploads_dir / task["src_path"])
    if task.get("result_path"):
        utils.remove_file(cfg.results_dir / task["result_path"])


def _recompute_usage() -> None:
    with db.session() as conn:
        rows = conn.execute(
            """SELECT user_id, COALESCE(SUM(src_size),0) AS total
                 FROM tasks WHERE status <> 'expired' GROUP BY user_id"""
        ).fetchall()
        for row in rows:
            conn.execute("UPDATE users SET used_bytes=? WHERE id=?", (row["total"], row["user_id"]))
        conn.execute(
            """UPDATE users SET used_bytes=0
                WHERE id NOT IN (SELECT DISTINCT user_id FROM tasks WHERE status <> 'expired')"""
        )


def _cleanup_tmp(active_ids: set[str]) -> int:
    removed = 0
    cutoff = utils.utcnow().timestamp() - TMP_GRACE_HOURS * 3600
    for path in cfg.tmp_dir.iterdir():
        if not path.is_dir() or path.name in active_ids:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                utils.remove_tree(path)
                removed += 1
        except OSError:
            continue
    return removed


def _cleanup_orphans() -> int:
    removed = 0
    tasks = models.all_tasks_light()
    active_src = {t["src_path"] for t in tasks if t["status"] != "expired"}
    active_results = {t["result_path"] for t in tasks if t["result_path"]}
    grace_ts = utils.utcnow().timestamp() - ORPHAN_GRACE_HOURS * 3600

    def old_enough(path: Path) -> bool:
        try:
            return path.stat().st_mtime < grace_ts
        except OSError:
            return False

    for entry in cfg.uploads_dir.iterdir():
        if entry.is_file() and entry.name not in active_src and old_enough(entry):
            utils.remove_file(entry)
            removed += 1
    for entry in cfg.results_dir.iterdir():
        if entry.is_dir():
            continue
        if entry.name not in active_results and old_enough(entry):
            utils.remove_file(entry)
            removed += 1
    return removed


def _cleanup_batch_tmp() -> int:
    removed = 0
    cutoff = utils.utcnow().timestamp() - 2 * 3600
    if cfg.batch_dir.is_dir():
        for entry in cfg.batch_dir.iterdir():
            try:
                if entry.stat().st_mtime < cutoff:
                    utils.remove_file(entry)
                    removed += 1
            except OSError:
                continue
    return removed


def _cleanup_task_logs(active_ids: set[str]) -> int:
    """删除已删除任务残留的日志；仍存在的任务日志保留。"""
    removed = 0
    cutoff = utils.utcnow().timestamp() - 7 * 86400
    if cfg.task_log_dir.is_dir():
        for entry in cfg.task_log_dir.glob("*.log"):
            try:
                if entry.stem not in active_ids and entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


def run_once() -> dict:
    cfg.ensure_dirs()
    db.init_db()
    now = utils.now_iso()
    summary = {
        "expired": 0, "stale_pending": 0, "stuck": 0, "shares": 0,
        "audit": 0, "alerts": 0, "orphans": 0, "tmp_dirs": 0, "batch": 0,
        "task_logs": 0,
    }

    # 1. 过期任务
    for task in models.list_expired_tasks(now):
        _remove_task_files(task)
        models.expire_task(task["id"])
        models.add_used_bytes(task["user_id"], -int(task["src_size"]))
        summary["expired"] += 1

    # 2. 上传后一直没选格式的僵尸任务（24 小时）
    for task in models.list_stale_pending(utils.iso_after_minutes(-24 * 60)):
        _remove_task_files(task)
        models.expire_task(task["id"])
        models.add_used_bytes(task["user_id"], -int(task["src_size"]))
        summary["stale_pending"] += 1

    running = models.list_running()
    active_ids = {t["id"] for t in running}
    for task in running:
        started = utils.parse_iso(task["started_at"])
        if started is None:
            continue
        limit = cfg.timeouts.get(task["category"], 600) + 300
        if (utils.utcnow() - started).total_seconds() > limit:
            _kill_task_container(task["id"])
            models.fail_task(task["id"], "任务长时间无响应，已被 cleanup 回收", auto_retry=False)
            models.add_alert("warning", "cleanup", f"回收失联任务 {task['id']}")
            summary["stuck"] += 1

    # 3. 过期 / 失效分享
    summary["shares"] = models.purge_expired_shares(now) + models.purge_inactive_shares()

    # 4. 旧审计 / 已确认告警 / 容器统计
    cutoff = utils.iso_after_minutes(-AUDIT_RETENTION_DAYS * 24 * 60)
    summary["audit"] = models.purge_audit(cutoff)
    summary["alerts"] = models.purge_alerts(cutoff, acked_only=False)
    models.purge_container_stats(utils.iso_after_minutes(-10))

    # 5. 临时目录 / 孤儿文件 / 批量下载包
    summary["tmp_dirs"] = _cleanup_tmp(active_ids)
    summary["orphans"] = _cleanup_orphans()
    summary["batch"] = _cleanup_batch_tmp()
    all_ids = {t["id"] for t in models.all_tasks_light()}
    summary["task_logs"] = _cleanup_task_logs(all_ids)

    # 6. 配额校准 + worker 失联 + 磁盘水位
    _recompute_usage()
    models.mark_stale_workers(utils.iso_after_minutes(-2))
    alerts.check_disks()

    log.info("清理完成：%s", summary)
    return summary


def main() -> None:
    stop = threading.Event()

    def handle_signal(signum, _frame):
        log.info("收到信号 %s，准备退出", signum)
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    once = os.environ.get("CLEANUP_ONCE") == "1"
    if once:
        run_once()
        return

    log.info("cleanup 启动，间隔 %s 秒", cfg.cleanup_interval)
    while not stop.is_set():
        try:
            run_once()
        except Exception:
            log.exception("清理任务异常")
        stop.wait(cfg.cleanup_interval)


if __name__ == "__main__":
    main()
