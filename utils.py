"""通用小工具：时间、文件、格式化。"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_after(seconds: int) -> str:
    return (utcnow() + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_after_hours(hours: int) -> str:
    return iso_after(hours * 3600)


def iso_after_minutes(minutes: int) -> str:
    return iso_after(minutes * 60)


def parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def to_display(value, tz_name: str = "Asia/Shanghai") -> str:
    """把数据库里的 UTC 时间转成展示时区。"""
    dt = parse_iso(value)
    if dt is None:
        return "-"
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def human_size(num) -> str:
    try:
        num = float(num or 0)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.2f} {unit}"
        num /= 1024
    return f"{num:.2f} TB"


def safe_filename(name: str, fallback: str = "file") -> str:
    """只保留 basename，去掉控制字符与路径分隔符。"""
    name = (name or "").replace("\\", "/")
    name = Path(name).name
    name = _CTRL_RE.sub("", name).strip().strip(".")
    name = name[:150]
    return name or fallback


def filename_ext(name: str) -> str:
    """返回规范化扩展名；.tar.gz 视为一个整体。"""
    lower = (name or "").lower().strip()
    if lower.endswith(".tar.gz"):
        return "tar.gz"
    suffix = Path(lower).suffix.lower()
    ext = suffix[1:] if suffix else ""
    return {"jpeg": "jpg", "tif": "tiff"}.get(ext, ext)


def json_dumps(data) -> str:
    return json.dumps(data or {}, ensure_ascii=False, separators=(",", ":"))


def json_loads(text) -> dict:
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def tail_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return "...(已截断)\n" + text[-limit:]


def read_text_head(path: Path, size: int = 4096) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(size)


def remove_file(path) -> bool:
    try:
        p = Path(path)
        if p.is_file():
            p.unlink()
            return True
    except OSError:
        pass
    return False


def remove_tree(path) -> bool:
    import shutil

    try:
        p = Path(path)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            return True
    except OSError:
        pass
    return False


def dir_size(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
    except OSError:
        return 0
    return total
