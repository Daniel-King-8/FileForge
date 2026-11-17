"""FileForge 全局配置。

所有路径与限额都可以通过环境变量覆盖：
- 容器内由 docker-compose.yml 注入 /srv/... 路径；
- 本地开发 / 冒烟测试直接使用项目下 runtime/ 目录。
"""

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return (Path(value) if value else default).expanduser().resolve()


def _mb(name: str, default_mb: int) -> int:
    return _int(name, default_mb) * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    # ---- 基础 ----
    app_name: str
    secret_key: str
    display_tz: str
    secure_cookies: bool
    session_hours: int
    base_dir: Path

    # ---- 目录 ----
    data_dir: Path
    uploads_dir: Path
    tmp_dir: Path
    results_dir: Path
    logs_dir: Path
    db_path: Path

    # ---- 上传 / 配额 ----
    max_upload_bytes: int
    user_quota_bytes: int
    result_ttl_hours: int
    disk_warn_pct: int
    disk_stop_pct: int

    # ---- 队列 ----
    max_concurrent: int
    retry_max: int
    timeouts: dict
    cleanup_interval: int
    task_log_max_bytes: int

    # ---- 工具容器 ----
    docker_bin: str
    tmp_volume: str
    tool_mount: str
    tool_uid: int
    tool_gid: int
    tool_mem: str
    tool_cpus: str
    tool_pids: int
    ffmpeg_image: str
    office_image: str
    archive_image: str
    archive_max_files: int
    archive_max_bytes: int

    # ---- 账号安全 ----
    admin_username: str
    admin_email: str
    admin_password: str
    login_max_failures: int
    login_window_minutes: int
    login_lock_minutes: int

    # ---- 分享 / 批量 ----
    share_max_downloads: int
    max_batch_download: int

    def ensure_dirs(self) -> None:
        # results 需要让 nginx worker（非 fileforge 用户）能读取，
        # 其余目录保持 750，仅 app/worker/cleanup 可访问。
        public_dirs = {self.results_dir, self.results_dir / "_tmp"}
        for path in (
            self.data_dir,
            self.uploads_dir,
            self.tmp_dir,
            self.results_dir,
            self.results_dir / "_tmp",
            self.logs_dir,
            self.logs_dir / "tasks",
        ):
            path.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(path, 0o755 if path in public_dirs else 0o750)
            except OSError:
                pass
        return None

    @property
    def batch_dir(self) -> Path:
        return self.results_dir / "_tmp"

    @property
    def task_log_dir(self) -> Path:
        return self.logs_dir / "tasks"


def load_settings() -> Settings:
    data_dir = _path("DATA_DIR", BASE_DIR / "runtime" / "data")
    return Settings(
        app_name="FileForge",
        secret_key=_str("SECRET_KEY", "dev-only-insecure-secret-key"),
        display_tz=_str("DISPLAY_TZ", "Asia/Shanghai"),
        secure_cookies=_bool("SECURE_COOKIES", False),
        session_hours=_int("SESSION_HOURS", 12),
        base_dir=BASE_DIR,
        data_dir=data_dir,
        uploads_dir=_path("UPLOADS_DIR", BASE_DIR / "runtime" / "uploads"),
        tmp_dir=_path("TMP_DIR", BASE_DIR / "runtime" / "tmp"),
        results_dir=_path("RESULTS_DIR", BASE_DIR / "runtime" / "results"),
        logs_dir=_path("LOGS_DIR", BASE_DIR / "runtime" / "logs"),
        db_path=data_dir / "fileforge.db",
        max_upload_bytes=_mb("MAX_UPLOAD_MB", 200),
        user_quota_bytes=_mb("USER_QUOTA_MB", 2048),
        result_ttl_hours=_int("RESULT_TTL_HOURS", 24),
        disk_warn_pct=_int("DISK_WARN_PCT", 85),
        disk_stop_pct=_int("DISK_STOP_PCT", 95),
        max_concurrent=_int("MAX_CONCURRENT", 2),
        retry_max=_int("RETRY_MAX", 1),
        timeouts={
            "image": _int("TIMEOUT_IMAGE", 60),
            "document": _int("TIMEOUT_DOCUMENT", 180),
            "media": _int("TIMEOUT_MEDIA", 600),
            "archive": _int("TIMEOUT_ARCHIVE", 120),
        },
        cleanup_interval=_int("CLEANUP_INTERVAL", 3600),
        task_log_max_bytes=_int("TASK_LOG_MAX_BYTES", 256 * 1024),
        docker_bin=_str("DOCKER_BIN", "docker"),
        tmp_volume=_str("TMP_VOLUME", "fileforge_tmp"),
        tool_mount=_str("TOOL_MOUNT", "subpath"),
        tool_uid=_int("TOOL_UID", 1000),
        tool_gid=_int("TOOL_GID", 1000),
        tool_mem=_str("TOOL_MEMORY", "1g"),
        tool_cpus=_str("TOOL_CPUS", "1"),
        tool_pids=_int("TOOL_PIDS_LIMIT", 64),
        ffmpeg_image=_str("FFMPEG_IMAGE", "fileforge/media-tools:1.0.0"),
        office_image=_str("OFFICE_IMAGE", "fileforge/office-tools:1.0.0"),
        archive_image=_str("ARCHIVE_IMAGE", "fileforge/media-tools:1.0.0"),
        archive_max_files=_int("ARCHIVE_MAX_FILES", 5000),
        archive_max_bytes=_mb("ARCHIVE_MAX_MB", 2048),
        admin_username=_str("ADMIN_USERNAME", "admin"),
        admin_email=_str("ADMIN_EMAIL", "admin@fileforge.local"),
        admin_password=_str("ADMIN_PASSWORD", ""),
        login_max_failures=_int("LOGIN_MAX_FAILURES", 5),
        login_window_minutes=_int("LOGIN_WINDOW_MINUTES", 15),
        login_lock_minutes=_int("LOGIN_LOCK_MINUTES", 15),
        share_max_downloads=_int("SHARE_MAX_DOWNLOADS", 10),
        max_batch_download=_int("MAX_BATCH_DOWNLOAD", 20),
    )


cfg = load_settings()
