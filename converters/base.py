"""转换容器封装。

worker 不内置 ffmpeg / LibreOffice，而是通过 docker CLI 启动一次性工具容器：

    docker run --rm --network none \
      --memory 1g --memory-swap 1g --cpus 1 --pids-limit 64 \
      --user 1000:1000 --tmpfs /tmp:rw,size=512m,mode=1777 \
      --mount type=volume,source=fileforge_tmp,target=/work,volume-subpath=<task_id> \
      -w /work 镜像 命令...

超时或取消时用 `docker kill` 结束容器，`--rm` 保证容器被自动删除。
"""

import logging
import subprocess
import threading
import time
import uuid
from collections import deque

import config

log = logging.getLogger("fileforge.converter")

task_log = logging.getLogger("fileforge.task")


class ConvertError(Exception):
    """转换失败（可读原因）。"""


class ConvertTimeout(ConvertError):
    """转换超时，容器已被 kill。"""


class ConvertCancelled(ConvertError):
    """用户取消或任务被删除。"""


class TaskLog:
    """每个任务一份日志文件，超过上限自动截断保留尾部。"""

    def __init__(self, task_id: str):
        cfg = config.cfg
        cfg.task_log_dir.mkdir(parents=True, exist_ok=True)
        self.path = cfg.task_log_dir / f"{task_id}.log"
        self.max_bytes = cfg.task_log_max_bytes
        self.fh = open(self.path, "w", encoding="utf-8", errors="replace")
        self.size = 0

    def write(self, message) -> None:
        text = str(message).rstrip("\n")
        if not text:
            return
        self.fh.write(text + "\n")
        self.fh.flush()
        self.size += len(text.encode("utf-8", "replace")) + 1
        if self.size > self.max_bytes:
            self._truncate()

    def _truncate(self) -> None:
        try:
            self.fh.close()
            data = self.path.read_bytes()[-self.max_bytes // 2:]
            with open(self.path, "wb") as fh:
                fh.write("...(日志过长，已保留尾部)\n".encode("utf-8") + data)
            self.size = len(data)
            self.fh = open(self.path, "a", encoding="utf-8", errors="replace")
        except OSError:
            self.fh = open(self.path, "a", encoding="utf-8", errors="replace")

    def close(self) -> None:
        try:
            self.fh.close()
        except OSError:
            pass


def log_tail(task_id: str, limit: int = 20000) -> str:
    path = config.cfg.task_log_dir / f"{task_id}.log"
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    text = data[-limit:].decode("utf-8", "replace")
    if len(data) > limit:
        text = "...(仅显示尾部)\n" + text
    return text


def _mount_args(task_id: str, mode: str) -> list[str]:
    cfg = config.cfg
    if mode == "subpath":
        return [
            "--mount",
            f"type=volume,source={cfg.tmp_volume},target=/work,volume-subpath={task_id}",
        ]
    return ["--mount", f"type=volume,source={cfg.tmp_volume},target=/work"]


def _container_workdir(task_id: str, mode: str, subdir: str | None) -> str:
    base = "/work" if mode == "subpath" else f"/work/{task_id}"
    return f"{base}/{subdir.strip('/')}" if subdir else base


def ensure_image_ready(image: str) -> None:
    cfg = config.cfg
    try:
        result = subprocess.run(
            [cfg.docker_bin, "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except FileNotFoundError as exc:
        raise ConvertError(
            "worker 内没有找到 docker 命令：请确认镜像已内置 docker CLI 且挂载了 /var/run/docker.sock"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ConvertError("查询工具镜像超时，Docker 守护进程可能不可用") from exc
    if result.returncode != 0:
        raise ConvertError(
            f"工具镜像 {image} 不存在，请先在宿主机执行：docker compose --profile tools build"
        )


def _kill_container(name: str) -> None:
    cfg = config.cfg
    for args in (
        [cfg.docker_bin, "kill", name],
        [cfg.docker_bin, "rm", "-f", name],
    ):
        try:
            subprocess.run(args, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            continue


def _execute(image: str, cmd: list[str], task_id: str, timeout: int, mode: str, *,
             logger: TaskLog | None, on_line, should_cancel, entrypoint: str | None,
             subdir: str | None, extra_env: dict | None, mem: str, cpus: str,
             capture_all: bool = False) -> str:
    cfg = config.cfg
    name = f"ff-{task_id[:8]}-{uuid.uuid4().hex[:6]}"
    args = [
        cfg.docker_bin, "run", "--rm", "--name", name,
        "--network", "none",
        "--memory", mem, "--memory-swap", mem,
        "--cpus", cpus,
        "--pids-limit", str(cfg.tool_pids),
        "--user", f"{cfg.tool_uid}:{cfg.tool_gid}",
        "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp:rw,size=512m,mode=1777",
        "-w", _container_workdir(task_id, mode, subdir),
        *_mount_args(task_id, mode),
    ]
    env = {"HOME": "/tmp"}
    env.update(extra_env or {})
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    if entrypoint:
        args += ["--entrypoint", entrypoint]
    args += [image, *cmd]

    if logger:
        logger.write(f"$ docker run --rm --network none --memory {mem} --cpus {cpus} "
                     f"--pids-limit {cfg.tool_pids} {image} {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise ConvertError("docker CLI 不可用，无法启动转换容器") from exc
    except OSError as exc:
        raise ConvertError(f"启动转换容器失败：{exc}") from exc

    output = deque() if capture_all else deque(maxlen=600)
    state = {"timeout": False, "cancel": False}
    stop_watch = threading.Event()

    def watchdog() -> None:
        started = time.monotonic()
        while not stop_watch.wait(0.5):
            if proc.poll() is not None:
                return
            if time.monotonic() - started > timeout:
                state["timeout"] = True
                if logger:
                    logger.write(f"[timeout] 超过 {timeout}s，执行 docker kill {name}")
                _kill_container(name)
                return
            if should_cancel and should_cancel():
                state["cancel"] = True
                if logger:
                    logger.write(f"[cancel] 任务被取消，执行 docker kill {name}")
                _kill_container(name)
                return

    watcher = threading.Thread(target=watchdog, daemon=True, name=f"watch-{task_id[:8]}")
    watcher.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            output.append(line)
            if logger:
                logger.write(line)
            if on_line:
                try:
                    on_line(line)
                except Exception:  # 进度解析异常不能影响转换
                    log.exception("进度回调异常 task=%s", task_id)
    finally:
        stop_watch.set()
        watcher.join(timeout=5)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            _kill_container(name)
            proc.kill()

    tail = "\n".join(list(output)[-30:])

    if state["timeout"]:
        raise ConvertTimeout(f"转换超时（{timeout}s），容器已被 kill")
    if state["cancel"]:
        raise ConvertCancelled("任务已取消")
    if proc.returncode != 0:
        raise ConvertError(f"转换容器退出码 {proc.returncode}\n{tail}")
    return "\n".join(output)


def run_tool_container(image: str, cmd: list[str], task_id: str, timeout: int, *,
                       logger: TaskLog | None = None, on_line=None, should_cancel=None,
                       entrypoint: str | None = None, subdir: str | None = None,
                       extra_env: dict | None = None, mem: str | None = None,
                       cpus: str | None = None, capture_all: bool = False) -> str:
    """运行一次性工具容器，返回合并后的输出（最多保留尾部 600 行）。"""
    cfg = config.cfg
    ensure_image_ready(image)
    mem = mem or cfg.tool_mem
    cpus = cpus or cfg.tool_cpus

    modes = ["subpath", "volume"] if cfg.tool_mount != "volume" else ["volume"]
    last_error: ConvertError | None = None
    for mode in modes:
        try:
            return _execute(
                image, cmd, task_id, timeout, mode,
                logger=logger, on_line=on_line, should_cancel=should_cancel,
                entrypoint=entrypoint, subdir=subdir, extra_env=extra_env,
                mem=mem, cpus=cpus, capture_all=capture_all,
            )
        except ConvertError as exc:
            message = str(exc).lower()
            if mode == "subpath" and any(key in message for key in ("subpath", "invalid mount")):
                if logger:
                    logger.write("[warn] 当前 Docker 不支持 volume-subpath，降级为整卷挂载")
                last_error = exc
                continue
            raise
    raise last_error or ConvertError("转换容器启动失败")


def make_progress_callback(task_id: str, logger: TaskLog | None = None, step: int = 2):
    """生成节流后的进度回调，返回 (callback, 当前进度)。"""
    state = {"last": 0}

    def update(percent: int) -> None:
        value = max(0, min(99, int(percent)))
        if value >= 100:
            value = 99
        if value - state["last"] < step:
            return
        state["last"] = value
        import models

        models.set_progress(task_id, value)
        if logger:
            logger.write(f"[progress] {value}%")

    return update, state


__all__ = [
    "ConvertError",
    "ConvertTimeout",
    "ConvertCancelled",
    "TaskLog",
    "log_tail",
    "run_tool_container",
    "ensure_image_ready",
    "make_progress_callback",
]
