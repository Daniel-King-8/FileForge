"""数据访问层：用户、任务、分享、审计、告警、worker 与容器统计。"""

import sqlite3
import uuid

import db
import utils

PAGE_SIZE = 15


def _rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _page_meta(total: int, page: int, per: int) -> dict:
    pages = max(1, (total + per - 1) // per)
    return {"total": total, "page": page, "per": per, "pages": pages}


def _page_bounds(page, per) -> tuple[int, int, int]:
    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per = max(1, min(100, int(per or PAGE_SIZE)))
    except (TypeError, ValueError):
        per = PAGE_SIZE
    return page, per, (page - 1) * per


# ---------------------------------------------------------------- users


def create_user(username: str, email: str, password_hash: str, role: str = "user",
                quota_bytes: int | None = None) -> dict:
    import config

    now = utils.now_iso()
    quota = config.cfg.user_quota_bytes if quota_bytes is None else quota_bytes
    with db.session() as conn:
        cur = conn.execute(
            """INSERT INTO users (username, email, password_hash, role, quota_bytes,
                                  used_bytes, status, created_at)
               VALUES (?, ?, ?, ?, ?, 0, 'active', ?)""",
            (username, email, password_hash, role, quota, now),
        )
        user_id = cur.lastrowid
        return dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


def get_user(user_id) -> dict | None:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username: str) -> dict | None:
    with db.session() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str) -> dict | None:
    with db.session() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email=? COLLATE NOCASE", (email,)
        ).fetchone()
        return dict(row) if row else None


def list_users(page=1, per=20, query: str = "") -> dict:
    page, per, offset = _page_bounds(page, per)
    where, params = "", []
    if query:
        where = "WHERE username LIKE ? OR email LIKE ?"
        params = [f"%{query}%", f"%{query}%"]
    with db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM users {where}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT u.*, (SELECT COUNT(*) FROM tasks t WHERE t.user_id=u.id) AS task_count
                FROM users u {where} ORDER BY u.id LIMIT ? OFFSET ?""",
            (*params, per, offset),
        ).fetchall()
    return {"items": _rows(rows), **_page_meta(total, page, per)}


def count_users() -> int:
    with db.session() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def set_user_status(user_id, status: str) -> None:
    with db.session() as conn:
        conn.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))


def update_password(user_id, password_hash: str) -> None:
    with db.session() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))


def add_used_bytes(user_id, delta: int) -> None:
    with db.session() as conn:
        conn.execute(
            "UPDATE users SET used_bytes=MAX(0, used_bytes + ?) WHERE id=?",
            (int(delta), user_id),
        )


def reset_used_bytes(user_id) -> None:
    with db.session() as conn:
        conn.execute("UPDATE users SET used_bytes=0 WHERE id=?", (user_id,))


# ---------------------------------------------------------------- tasks


def create_task(user_id: int, src_name: str, src_path: str, src_size: int,
                src_mime: str, category: str) -> dict:
    task_id = uuid.uuid4().hex
    now = utils.now_iso()
    with db.session() as conn:
        conn.execute(
            """INSERT INTO tasks (id, user_id, src_name, src_path, src_size, src_mime,
                                  category, status, progress, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)""",
            (task_id, user_id, src_name, src_path, src_size, src_mime, category, now),
        )
    return get_task(task_id)


def get_task(task_id: str) -> dict | None:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def get_task_for_user(task_id: str, user_id=None, is_admin: bool = False) -> dict | None:
    task = get_task(task_id)
    if not task:
        return None
    if is_admin or task["user_id"] == user_id:
        return task
    return None


def batch_get_tasks(task_ids: list[str], user_id=None, is_admin: bool = False) -> list[dict]:
    if not task_ids:
        return []
    marks = ",".join("?" for _ in task_ids)
    sql = f"SELECT * FROM tasks WHERE id IN ({marks})"
    params: list = list(task_ids)
    if not is_admin:
        sql += " AND user_id=?"
        params.append(user_id)
    with db.session() as conn:
        return _rows(conn.execute(sql, params).fetchall())


def list_tasks(user_id=None, status=None, category=None, query="", page=1, per=PAGE_SIZE) -> dict:
    page, per, offset = _page_bounds(page, per)
    clauses, params = [], []
    if user_id is not None:
        clauses.append("user_id=?")
        params.append(user_id)
    if status:
        clauses.append("status=?")
        params.append(status)
    if category:
        clauses.append("category=?")
        params.append(category)
    if query:
        clauses.append("src_name LIKE ?")
        params.append(f"%{query}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM tasks {where}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT * FROM tasks {where}
                ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?""",
            (*params, per, offset),
        ).fetchall()
    return {"items": _rows(rows), **_page_meta(total, page, per)}


def task_counts(user_id=None) -> dict:
    sql = "SELECT status, COUNT(*) AS c FROM tasks"
    params: list = []
    if user_id is not None:
        sql += " WHERE user_id=?"
        params.append(user_id)
    sql += " GROUP BY status"
    with db.session() as conn:
        rows = conn.execute(sql, params).fetchall()
    counts = {r["status"]: r["c"] for r in rows}
    counts["total"] = sum(counts.values())
    return counts


def claim_task(worker_id: str, max_concurrent: int) -> dict | None:
    """原子领取一个待转换任务；SQLite 写锁 + 全局并发上限。"""
    now = utils.now_iso()
    with db.session() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                """UPDATE tasks
                      SET status='running', worker_id=?, started_at=?, progress=0, error_msg=NULL
                    WHERE status='pending'
                      AND target_format IS NOT NULL AND target_format <> ''
                      AND (SELECT COUNT(*) FROM tasks WHERE status='running') < ?
                      AND id = (SELECT id FROM tasks
                                 WHERE status='pending'
                                   AND target_format IS NOT NULL AND target_format <> ''
                                 ORDER BY created_at ASC, rowid ASC LIMIT 1)
                    RETURNING *""",
                (worker_id, now, int(max_concurrent)),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    return dict(row) if row else None


def count_running() -> int:
    with db.session() as conn:
        return conn.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0]


def task_status(task_id: str) -> str | None:
    with db.session() as conn:
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        return row["status"] if row else None


def reset_for_convert(task_id: str, target_format: str, options_json: str) -> None:
    with db.session() as conn:
        conn.execute(
            """UPDATE tasks
                  SET target_format=?, options_json=?, status='pending', progress=0,
                      error_msg=NULL, result_name=NULL, result_path=NULL, result_size=NULL,
                      started_at=NULL, finished_at=NULL, expire_at=NULL, retry_count=0
                WHERE id=?""",
            (target_format, options_json, task_id),
        )


def reset_for_retry(task_id: str) -> None:
    with db.session() as conn:
        conn.execute(
            """UPDATE tasks
                  SET status='pending', progress=0, error_msg=NULL, started_at=NULL,
                      finished_at=NULL, expire_at=NULL, worker_id=NULL,
                      result_name=NULL, result_path=NULL, result_size=NULL, retry_count=0
                WHERE id=? AND status IN ('failed','canceled','expired')""",
            (task_id,),
        )


def set_progress(task_id: str, percent: int) -> None:
    value = max(0, min(99, int(percent)))
    with db.session() as conn:
        conn.execute(
            "UPDATE tasks SET progress=? WHERE id=? AND status='running'",
            (value, task_id),
        )


def finish_task(task_id: str, result_name: str, result_path: str, result_size: int) -> bool:
    """仅当任务仍在 running 时落结果；返回 False 表示任务已被取消/删除。"""
    import config

    now = utils.now_iso()
    expire_at = utils.iso_after(config.cfg.result_ttl_hours * 3600)
    with db.session() as conn:
        cur = conn.execute(
            """UPDATE tasks
                  SET status='done', progress=100, finished_at=?, expire_at=?,
                      result_name=?, result_path=?, result_size=?, error_msg=NULL
                WHERE id=? AND status='running'""",
            (now, expire_at, result_name, result_path, result_size, task_id),
        )
        return cur.rowcount > 0


def fail_task(task_id: str, error: str, auto_retry: bool = False) -> str:
    """失败落库；auto_retry 时重新排队并累加 retry_count。返回最终状态。"""
    import config

    message = utils.tail_text(str(error or "未知错误"), 2000)
    now = utils.now_iso()
    expire_at = utils.iso_after(config.cfg.result_ttl_hours * 3600)
    with db.session() as conn:
        if auto_retry:
            cur = conn.execute(
                """UPDATE tasks
                      SET status='pending', progress=0, error_msg=?, retry_count=retry_count+1,
                          worker_id=NULL, started_at=NULL
                    WHERE id=? AND status='running'""",
                (message, task_id),
            )
            return "pending" if cur.rowcount else "lost"
        cur = conn.execute(
            """UPDATE tasks
                  SET status='failed', error_msg=?, finished_at=?, expire_at=?, worker_id=NULL
                WHERE id=? AND status='running'""",
            (message, now, expire_at, task_id),
        )
        return "failed" if cur.rowcount else "lost"


def mark_canceled(task_id: str, user_id=None, is_admin: bool = False) -> bool:
    import config

    expire_at = utils.iso_after(config.cfg.result_ttl_hours * 3600)
    sql = ("UPDATE tasks SET status='canceled', finished_at=?, expire_at=? "
           "WHERE id=? AND status IN ('pending','running')")
    params: list = [utils.now_iso(), expire_at, task_id]
    if not is_admin:
        sql += " AND user_id=?"
        params.append(user_id)
    with db.session() as conn:
        return conn.execute(sql, params).rowcount > 0


def delete_task_row(task_id: str) -> None:
    with db.session() as conn:
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.execute("DELETE FROM shares WHERE task_id=?", (task_id,))


def list_expired_tasks(now: str) -> list[dict]:
    with db.session() as conn:
        rows = conn.execute(
            """SELECT * FROM tasks
                WHERE status IN ('done','failed','canceled')
                  AND expire_at IS NOT NULL AND expire_at < ?""",
            (now,),
        ).fetchall()
    return _rows(rows)


def list_stale_pending(before: str) -> list[dict]:
    with db.session() as conn:
        rows = conn.execute(
            """SELECT * FROM tasks
                WHERE status='pending' AND (target_format IS NULL OR target_format='')
                  AND created_at < ?""",
            (before,),
        ).fetchall()
    return _rows(rows)


def list_running() -> list[dict]:
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE status='running'").fetchall()
    return _rows(rows)


def expire_task(task_id: str, status: str = "expired") -> None:
    with db.session() as conn:
        conn.execute(
            """UPDATE tasks
                  SET status=?, finished_at=?, result_path=NULL, result_name=NULL, result_size=NULL
                WHERE id=?""",
            (status, utils.now_iso(), task_id),
        )


def all_tasks_light() -> list[dict]:
    with db.session() as conn:
        rows = conn.execute("SELECT id, src_path, result_path, status FROM tasks").fetchall()
    return _rows(rows)


# ---------------------------------------------------------------- shares


def create_share(task_id: str, expire_at: str | None, max_downloads: int = 0) -> dict:
    token = uuid.uuid4().hex
    with db.session() as conn:
        conn.execute("DELETE FROM shares WHERE task_id=?", (task_id,))
        conn.execute(
            """INSERT INTO shares (token, task_id, expire_at, max_downloads, download_count, created_at)
               VALUES (?, ?, ?, ?, 0, ?)""",
            (token, task_id, expire_at, max(0, int(max_downloads or 0)), utils.now_iso()),
        )
        return dict(conn.execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone())


def get_share(token: str) -> dict | None:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()
        return dict(row) if row else None


def incr_share_download(token: str) -> None:
    with db.session() as conn:
        conn.execute("UPDATE shares SET download_count=download_count+1 WHERE token=?", (token,))


def list_expired_shares(now: str) -> list[dict]:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT * FROM shares WHERE expire_at IS NOT NULL AND expire_at < ?", (now,)
        ).fetchall()
    return _rows(rows)


def purge_share(token: str) -> None:
    with db.session() as conn:
        conn.execute("DELETE FROM shares WHERE token=?", (token,))


def purge_expired_shares(now: str) -> int:
    with db.session() as conn:
        return conn.execute(
            "DELETE FROM shares WHERE expire_at IS NOT NULL AND expire_at < ?", (now,)
        ).rowcount


def purge_inactive_shares() -> int:
    """删除任务已过期/失败/取消或已删除的分享记录。"""
    with db.session() as conn:
        return conn.execute(
            """DELETE FROM shares
                WHERE task_id NOT IN (SELECT id FROM tasks WHERE status='done')"""
        ).rowcount


# ---------------------------------------------------------------- audit


def audit(user_id, action: str, target: str = "", ip: str = "", ua: str = "") -> None:
    with db.session() as conn:
        conn.execute(
            """INSERT INTO audit_logs (user_id, action, target, ip, ua, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, action, str(target or "")[:200], (ip or "")[:64], (ua or "")[:300],
             utils.now_iso()),
        )


def count_login_failures(username: str, ip: str, since: str) -> int:
    with db.session() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM audit_logs
                WHERE action='login_failed' AND target=? AND ip=? AND created_at >= ?""",
            (username, ip, since),
        ).fetchone()[0]


def last_login_failure(username: str, ip: str) -> str | None:
    with db.session() as conn:
        row = conn.execute(
            """SELECT MAX(created_at) FROM audit_logs
                WHERE action='login_failed' AND target=? AND ip=?""",
            (username, ip),
        ).fetchone()
        return row[0] if row else None


def recent_audit(limit: int = 50) -> list[dict]:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return _rows(rows)


def purge_audit(before: str) -> int:
    with db.session() as conn:
        return conn.execute("DELETE FROM audit_logs WHERE created_at < ?", (before,)).rowcount


# ---------------------------------------------------------------- alerts


def add_alert(level: str, source: str, message: str) -> None:
    with db.session() as conn:
        conn.execute(
            "INSERT INTO alerts (level, source, message, created_at, acked) VALUES (?, ?, ?, ?, 0)",
            (level, source, message, utils.now_iso()),
        )


def has_recent_alert(message: str, since: str) -> bool:
    with db.session() as conn:
        row = conn.execute(
            "SELECT 1 FROM alerts WHERE message=? AND created_at >= ? LIMIT 1", (message, since)
        ).fetchone()
        return row is not None


def list_alerts(limit: int = 50, unacked_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM alerts"
    if unacked_only:
        sql += " WHERE acked=0"
    sql += " ORDER BY id DESC LIMIT ?"
    with db.session() as conn:
        return _rows(conn.execute(sql, (limit,)).fetchall())


def count_unacked_alerts() -> int:
    with db.session() as conn:
        return conn.execute("SELECT COUNT(*) FROM alerts WHERE acked=0").fetchone()[0]


def ack_alert(alert_id: int) -> None:
    with db.session() as conn:
        conn.execute("UPDATE alerts SET acked=1 WHERE id=?", (alert_id,))


def purge_alerts(before: str, acked_only: bool = True) -> int:
    sql = "DELETE FROM alerts WHERE created_at < ?"
    if acked_only:
        sql += " AND acked=1"
    with db.session() as conn:
        return conn.execute(sql, (before,)).rowcount


# ---------------------------------------------------------------- workers


def heartbeat(worker_id: str, current_task=None, status: str = "online") -> None:
    now = utils.now_iso()
    with db.session() as conn:
        conn.execute(
            """INSERT INTO workers (id, container_id, last_heartbeat, current_task, status)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   last_heartbeat=excluded.last_heartbeat,
                   current_task=excluded.current_task,
                   status=excluded.status""",
            (worker_id, worker_id, now, current_task, status),
        )


def list_workers() -> list[dict]:
    with db.session() as conn:
        return _rows(conn.execute("SELECT * FROM workers ORDER BY id").fetchall())


def mark_stale_workers(before: str) -> int:
    with db.session() as conn:
        return conn.execute(
            """UPDATE workers SET status='offline'
                WHERE status='online' AND (last_heartbeat IS NULL OR last_heartbeat < ?)""",
            (before,),
        ).rowcount


# ---------------------------------------------------------------- container stats


def replace_container_stats(rows: list[dict]) -> None:
    now = utils.now_iso()
    cutoff = utils.iso_after_minutes(-10)
    with db.session() as conn:
        conn.execute("DELETE FROM container_stats WHERE created_at < ?", (cutoff,))
        for row in rows:
            conn.execute(
                """INSERT INTO container_stats (name, cpu_pct, mem_usage, mem_pct, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (row.get("name"), row.get("cpu_pct"), row.get("mem_usage"),
                 row.get("mem_pct"), now),
            )


def latest_container_stats() -> list[dict]:
    with db.session() as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM container_stats ORDER BY id DESC LIMIT 100"
        ).fetchall())
    seen, latest = set(), []
    for row in rows:
        if row["name"] in seen:
            continue
        seen.add(row["name"])
        latest.append(row)
    return latest


def purge_container_stats(before: str) -> int:
    with db.session() as conn:
        return conn.execute("DELETE FROM container_stats WHERE created_at < ?", (before,)).rowcount


# ---------------------------------------------------------------- admin overview


def overview() -> dict:
    with db.session() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        banned = conn.execute("SELECT COUNT(*) FROM users WHERE status='banned'").fetchone()[0]
        tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        shares = conn.execute("SELECT COUNT(*) FROM shares").fetchone()[0]
        uploads_bytes = conn.execute(
            "SELECT COALESCE(SUM(src_size),0) FROM tasks WHERE status NOT IN ('expired')"
        ).fetchone()[0]
        results_bytes = conn.execute(
            "SELECT COALESCE(SUM(result_size),0) FROM tasks WHERE status='done'"
        ).fetchone()[0]
    return {
        "users": users,
        "banned": banned,
        "tasks": tasks,
        "shares": shares,
        "uploads_bytes": uploads_bytes,
        "results_bytes": results_bytes,
    }
