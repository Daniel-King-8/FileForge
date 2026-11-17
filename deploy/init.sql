-- FileForge 数据库结构（SQLite，WAL 模式由应用连接时开启）
-- 所有时间字段均为 UTC ISO-8601 字符串，例如 2026-11-17T08:30:00Z

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',      -- user | admin
    quota_bytes   INTEGER NOT NULL DEFAULT 2147483648,
    used_bytes    INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'active',    -- active | banned
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT    PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    src_name      TEXT    NOT NULL,
    src_path      TEXT    NOT NULL,                     -- uploads/ 下的文件名
    src_size      INTEGER NOT NULL,
    src_mime      TEXT,
    target_format TEXT,                                 -- 为空表示已上传、等待选择格式
    category      TEXT    NOT NULL,                     -- image|document|media|archive
    options_json  TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending',    -- pending|running|done|failed|expired|canceled
    progress      INTEGER NOT NULL DEFAULT 0,
    error_msg     TEXT,
    result_name   TEXT,
    result_path   TEXT,                                 -- results/ 下的文件名
    result_size   INTEGER,
    worker_id     TEXT,
    created_at    TEXT    NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    expire_at     TEXT,
    retry_count   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at);

CREATE TABLE IF NOT EXISTS shares (
    token          TEXT    PRIMARY KEY,
    task_id        TEXT    NOT NULL,
    expire_at      TEXT,
    max_downloads  INTEGER NOT NULL DEFAULT 0,          -- 0 表示不限次数
    download_count INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shares_task ON shares(task_id);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    action     TEXT NOT NULL,
    target     TEXT,
    ip         TEXT,
    ua         TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action_target ON audit_logs(action, target, created_at);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    level      TEXT    NOT NULL,                        -- info|warning|critical
    source     TEXT    NOT NULL,                        -- app|worker|cleanup|nginx
    message    TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    acked      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS workers (
    id             TEXT PRIMARY KEY,                    -- 容器 hostname
    container_id   TEXT,
    last_heartbeat TEXT,
    current_task   TEXT,
    status         TEXT NOT NULL DEFAULT 'online'       -- online|offline
);

-- 扩展表：worker 定时采集 docker stats，供管理后台展示，避免把 docker.sock 暴露给 app
CREATE TABLE IF NOT EXISTS container_stats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    cpu_pct    TEXT,
    mem_usage  TEXT,
    mem_pct    TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_container_stats_created ON container_stats(created_at);
