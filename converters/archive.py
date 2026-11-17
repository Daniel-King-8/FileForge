"""压缩包：7z 容器解包/重打包，含 zip 炸弹与路径穿越防护。"""

import os
import re
import tarfile
import zipfile
from pathlib import Path

import config
import utils

from .base import ConvertCancelled, ConvertError, run_tool_container

DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _parse_listing(text: str) -> list[dict]:
    entries = []
    for block in text.split("\n\n"):
        data = {}
        for line in block.splitlines():
            if " = " in line:
                key, _, value = line.partition(" = ")
                data[key.strip()] = value.strip()
        path = data.get("Path")
        if not path:
            continue
        if "Type" in data and "Size" not in data:
            continue  # 归档头部
        folder = data.get("Folder") == "+"
        try:
            size = int(data.get("Size") or 0)
        except ValueError:
            size = 0
        entries.append({
            "path": path,
            "size": size,
            "folder": folder,
            "attrs": data.get("Attributes") or "",
            "encrypted": data.get("Encrypted") == "+",
        })
    return entries


def _check_entries(entries: list[dict], log) -> tuple[list[dict], int]:
    cfg = config.cfg
    files = [e for e in entries if not e["folder"]]
    total = sum(e["size"] for e in files)
    if len(files) > cfg.archive_max_files:
        raise ConvertError(f"压缩包内文件数 {len(files)} 超过上限 {cfg.archive_max_files}")
    if total > cfg.archive_max_bytes:
        raise ConvertError(
            f"解压后总大小 {utils.human_size(total)} 超过上限 "
            f"{utils.human_size(cfg.archive_max_bytes)}（疑似 zip 炸弹）"
        )
    for entry in entries:
        raw = entry["path"].replace("\\", "/")
        normalized = raw.strip("/")
        parts = [p for p in normalized.split("/") if p not in ("", ".")]
        if raw.startswith("/") or DRIVE_RE.match(raw) or ".." in parts:
            raise ConvertError(f"压缩包内含可疑路径：{entry['path']}（路径穿越），已拒绝")
        if entry["attrs"].startswith("l"):
            raise ConvertError(f"压缩包含符号链接：{entry['path']}，已拒绝")
        if entry["encrypted"]:
            raise ConvertError("暂不支持加密压缩包")
    log.write(f"[archive] 安全检查通过：{len(files)} 个文件，解压后 {utils.human_size(total)}")
    return files, total


def _list(task, src: Path, log, should_cancel, timeout: int) -> tuple[list[dict], str]:
    cfg = config.cfg
    output = run_tool_container(
        cfg.archive_image,
        ["l", "-slt", "-bd", "-y", src.name],
        task["id"], timeout, logger=log, should_cancel=should_cancel, capture_all=True,
    )
    entries = _parse_listing(output)
    _check_entries(entries, log)
    return entries, output


def _extract(task, src: Path, workdir: Path, log, should_cancel, timeout: int) -> Path:
    cfg = config.cfg
    extract_dir = workdir / "extract"
    if extract_dir.exists():
        utils.remove_tree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    run_tool_container(
        cfg.archive_image,
        ["x", "-y", "-bso0", "-bsp0", "-oextract", src.name],
        task["id"], timeout, logger=log, should_cancel=should_cancel,
    )
    # 二次校验：符号链接 / 实际落盘大小
    count = 0
    total = 0
    for root, dirs, files in os.walk(extract_dir):
        for name in dirs + files:
            path = Path(root) / name
            if path.is_symlink():
                raise ConvertError(f"解压结果包含符号链接：{path.name}，已拒绝")
        for name in files:
            path = Path(root) / name
            count += 1
            try:
                total += path.stat().st_size
            except OSError:
                continue
            if count > cfg.archive_max_files or total > cfg.archive_max_bytes:
                raise ConvertError("解压后超出文件数/大小上限，已中止")
    log.write(f"[archive] 解压完成：{count} 个文件，{utils.human_size(total)}")
    return extract_dir


def _pack_zip(extract_dir: Path, out: Path, level: int, should_cancel) -> None:
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=level) as zf:
        for path in sorted(extract_dir.rglob("*")):
            if should_cancel():
                raise ConvertCancelled("任务已取消")
            if path.is_file():
                zf.write(path, path.relative_to(extract_dir).as_posix())


def _pack_targz(extract_dir: Path, out: Path, level: int, should_cancel) -> None:
    with tarfile.open(out, "w:gz", compresslevel=level) as tf:
        for path in sorted(extract_dir.rglob("*")):
            if should_cancel():
                raise ConvertCancelled("任务已取消")
            if path.is_file():
                tf.add(path, arcname=path.relative_to(extract_dir).as_posix())


def convert(task, src: Path, outdir: Path, log, progress, should_cancel) -> Path:
    cfg = config.cfg
    options = utils.json_loads(task.get("options_json"))
    target = (task.get("target_format") or "").lower()
    try:
        level = max(1, min(9, int(options.get("level") or 6)))
    except (TypeError, ValueError):
        level = 6
    timeout = cfg.timeouts["archive"]
    workdir = src.parent

    progress(8)
    entries, _output = _list(task, src, log, should_cancel, timeout)
    progress(35)

    if target == "list":
        files = [e for e in entries if not e["folder"]]
        files.sort(key=lambda e: e["size"], reverse=True)
        payload = {
            "entry_count": len(files),
            "dir_count": len(entries) - len(files),
            "total_size": sum(e["size"] for e in files),
            "files": [{"path": e["path"], "size": e["size"]} for e in files[:800]],
            "truncated": len(files) > 800,
        }
        out = outdir / "result.json"
        out.write_text(utils.json_dumps(payload), encoding="utf-8")
        log.write(f"[archive] 已生成内容清单（{len(files)} 个文件）")
        progress(95)
        return out

    extract_dir = _extract(task, src, workdir, log, should_cancel, timeout)
    progress(60)

    if target == "zip":
        out = outdir / "result.zip"
        _pack_zip(extract_dir, out, level, should_cancel)
    elif target == "tar.gz":
        out = outdir / "result.tar.gz"
        _pack_targz(extract_dir, out, level, should_cancel)
    elif target == "7z":
        out = outdir / "result.7z"
        run_tool_container(
            cfg.archive_image,
            ["a", "-t7z", f"-mx={level}", "-bso0", "-bsp0", "../out/result.7z", "."],
            task["id"], timeout, logger=log, should_cancel=should_cancel,
            subdir="extract",
        )
    else:
        raise ConvertError(f"不支持的压缩包目标：{target}")

    if not out.is_file() or out.stat().st_size == 0:
        raise ConvertError("压缩包转换未生成有效文件")
    progress(95)
    log.write(f"[archive] 完成 {out.name} {utils.human_size(out.stat().st_size)}")
    return out
