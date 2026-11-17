"""队列 worker：领取任务 -> 调用转换器 -> 落结果。

支持多实例：`docker compose up -d --scale worker=2`。
任务领取依赖 SQLite 的原子 UPDATE ... RETURNING 与全局并发上限。
"""

import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path

import config
import db
import models
import utils
from converters import CONVERTERS
from converters.base import (
    ConvertCancelled,
    ConvertError,
    ConvertTimeout,
    TaskLog,
    make_progress_callback,
)

cfg = config.cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("fileforge.worker")

STATS_INTERVAL = 20


@contextmanager
def _time_limit(seconds: int, label: str):
    """Pillow 在 worker 进程内执行，用 SIGALRM 兜底超时；Windows 开发机自动跳过。"""
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def _raise(_signum, _frame):
        raise ConvertTimeout(f"{label}超时（{seconds}s）")

    previous = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _check_docker() -> None:
    """启动时检查 docker CLI 与 socket 权限，排错信息直接打到日志。"""
    try:
        result = subprocess.run(
            [cfg.docker_bin, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        log.error("找不到 docker 命令，worker 无法启动转换容器")
        return
    except subprocess.TimeoutExpired:
        log.error("docker 命令超时，请检查 /var/run/docker.sock")
        return
    if result.returncode != 0:
        log.error(
            "无法访问 Docker 守护进程（DOCKER_GID=%s）：%s。"
            "请确认挂载了 /var/run/docker.sock，且 group_add 与宿主机 socket 的 GID 一致。",
            os.environ.get("DOCKER_GID", "0"),
            (result.stderr or result.stdout).strip()[:300],
        )
    else:
        log.info("Docker 守护进程可用：%s", result.stdout.strip())


def _collect_container_stats() -> None:
    try:
        result = subprocess.run(
            [cfg.docker_bin, "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode != 0:
        return
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        name = parts[0].strip()
        if not name.startswith("fileforge"):
            continue
        rows.append({
            "name": name,
            "cpu_pct": parts[1].strip(),
            "mem_usage": parts[2].strip(),
            "mem_pct": parts[3].strip(),
        })
    if rows:
        models.replace_container_stats(rows)


def _heartbeat_loop(worker_id: str, state: dict, stop: threading.Event) -> None:
    last_stats = 0.0
    while not stop.wait(10):
        try:
            models.heartbeat(worker_id, state.get("task"))
            if time.monotonic() - last_stats >= STATS_INTERVAL:
                last_stats = time.monotonic()
                _collect_container_stats()
        except Exception:
            log.exception("心跳写入失败")


def _move_result(task: dict, result: Path) -> None:
    result_id = uuid.uuid4().hex
    dest = cfg.results_dir / result_id
    shutil.move(str(result), str(dest))
    try:
        size = dest.stat().st_size
    except OSError:
        size = 0
    stem = Path(utils.safe_filename(task["src_name"])).stem[:100] or "result"
    target = task["target_format"]
    ext = "json" if target == "list" else target
    name = f"{stem}.{ext}"

    if models.finish_task(task["id"], name, result_id, size):
        models.audit(task["user_id"], "convert_done", f"{task['id']} {target}", "", "")
        log.info("任务完成 %s -> %s (%s)", task["id"], name, utils.human_size(size))
    else:
        utils.remove_file(dest)
        log.info("任务 %s 状态已变化，丢弃结果", task["id"])


def _process(task: dict, worker_id: str, state: dict) -> None:
    task_id = task["id"]
    category = task["category"]
    workdir = cfg.tmp_dir / task_id
    logger = TaskLog(task_id)
    state["task"] = task_id

    try:
        utils.remove_tree(workdir)
        outdir = workdir / "out"
        outdir.mkdir(parents=True, exist_ok=True)

        logger.write(
            f"[fileforge] task={task_id} category={category} "
            f"target={task['target_format']} worker={worker_id}"
        )
        log.info("开始处理任务 %s (%s -> %s)", task_id, category, task["target_format"])

        src = cfg.uploads_dir / task["src_path"]
        if not src.is_file():
            raise ConvertError("源文件不存在或已被清理，请重新上传")

        ext = utils.filename_ext(task["src_name"])
        input_path = workdir / f"input.{ext}"
        shutil.copyfile(src, input_path)
        logger.write(
            f"[fileforge] 源文件 {task['src_name']} "
            f"({utils.human_size(task['src_size'])}) -> {task['target_format']}"
        )

        progress, _state = make_progress_callback(task_id, logger)
        should_cancel = lambda: models.task_status(task_id) != "running"

        converter = CONVERTERS.get(category)
        if converter is None:
            raise ConvertError(f"不支持的转换分类：{category}")

        if category == "image":
            # 容器转换器自己有 watchdog；Pillow 在 worker 内跑，需要额外兜底
            with _time_limit(cfg.timeouts["image"], "图片转换"):
                result = converter(task, input_path, outdir, logger, progress, should_cancel)
        else:
            result = converter(task, input_path, outdir, logger, progress, should_cancel)
        if should_cancel():
            raise ConvertCancelled("任务已取消")
        if not result.is_file() or result.stat().st_size <= 0:
            raise ConvertError("转换完成但没有生成有效结果")

        _move_result(task, result)
        logger.write("[fileforge] 转换流程结束")

    except ConvertCancelled as exc:
        logger.write(f"[cancel] {exc}")
        log.info("任务取消 %s", task_id)
        if models.task_status(task_id) == "running":
            models.fail_task(task_id, "任务已取消")
    except ConvertTimeout as exc:
        logger.write(f"[failed] {exc}")
        log.warning("任务超时 %s: %s", task_id, exc)
        models.fail_task(task_id, str(exc))
        models.add_alert("warning", "worker", f"任务 {task_id} 转换超时：{exc}")
        models.audit(task["user_id"], "convert_failed", f"{task_id} timeout", "", "")
    except ConvertError as exc:
        logger.write(f"[failed] {exc}")
        log.warning("任务失败 %s: %s", task_id, exc)
        retry = int(task.get("retry_count") or 0) < cfg.retry_max
        status = models.fail_task(task_id, str(exc), auto_retry=retry)
        if status == "pending":
            logger.write("[retry] 已自动重新排队（自动重试上限 %d 次）" % cfg.retry_max)
            log.info("任务 %s 自动重试", task_id)
        else:
            models.audit(task["user_id"], "convert_failed", f"{task_id} {exc}"[:200], "", "")
    except Exception as exc:
        detail = f"{exc}\n{traceback.format_exc()}"
        logger.write(f"[failed] 未预期异常：{detail}")
        log.exception("任务异常 %s", task_id)
        models.fail_task(task_id, detail)
        models.audit(task["user_id"], "convert_failed", f"{task_id} unexpected", "", "")
    finally:
        state["task"] = None
        utils.remove_tree(workdir)
        logger.close()


def main() -> None:
    cfg.ensure_dirs()
    db.init_db()

    worker_id = socket.gethostname()[:40] or f"worker-{os.getpid()}"
    stop = threading.Event()
    state: dict = {"task": None}

    def handle_signal(signum, _frame):
        log.info("收到信号 %s，处理完当前任务后退出", signum)
        stop.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    models.heartbeat(worker_id, None)
    threading.Thread(target=_heartbeat_loop, args=(worker_id, state, stop),
                     daemon=True, name="heartbeat").start()

    once = os.environ.get("WORKER_ONCE") == "1"
    log.info("worker 启动：id=%s 并发上限=%s docker=%s", worker_id,
             cfg.max_concurrent, cfg.docker_bin)
    _check_docker()

    try:
        while not stop.is_set():
            try:
                if models.count_running() >= cfg.max_concurrent:
                    stop.wait(1.0)
                    continue
                task = models.claim_task(worker_id, cfg.max_concurrent)
                if task is None:
                    if once:
                        break
                    stop.wait(1.2)
                    continue
                _process(task, worker_id, state)
                if once:
                    break
            except Exception:
                log.exception("worker 主循环异常")
                stop.wait(2.0)
    finally:
        stop.set()
        try:
            models.heartbeat(worker_id, None, "offline")
        except Exception:
            pass
        log.info("worker 退出：%s", worker_id)


if __name__ == "__main__":
    main()
