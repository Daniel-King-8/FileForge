"""格式白名单：前后端共用，避免两处定义漂移。"""

CATEGORIES = {
    "image": {
        "label": "图片转换",
        "tagline": "JPG / PNG / WebP / GIF / TIFF / BMP 互转，可压缩、缩放",
        "exts": ["jpg", "jpeg", "png", "webp", "gif", "tiff", "tif", "bmp"],
        "targets": ["jpg", "png", "webp", "gif", "tiff", "bmp"],
        "options": ["quality", "width", "height"],
        "mime": {
            "jpg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
            "gif": "image/gif",
            "tiff": "image/tiff",
            "bmp": "image/bmp",
        },
    },
    "document": {
        "label": "文档转换",
        "tagline": "Word / Excel / PPT / Markdown 转 PDF、CSV、HTML",
        "exts": ["docx", "doc", "xlsx", "xls", "pptx", "ppt", "odt", "ods", "odp", "md", "txt", "html"],
        "targets": ["pdf", "csv", "html", "txt"],
        "options": [],
        "mime": {
            "pdf": "application/pdf",
            "csv": "text/csv; charset=utf-8",
            "html": "text/html; charset=utf-8",
            "txt": "text/plain; charset=utf-8",
        },
    },
    "media": {
        "label": "音视频转换",
        "tagline": "视频格式互转、提取音频、转 GIF、截取片段",
        "exts": ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v", "mp3", "wav", "flac", "aac", "ogg", "m4a"],
        "targets": ["mp4", "mkv", "webm", "avi", "gif", "mp3", "m4a", "wav", "flac", "aac", "ogg"],
        "options": ["start", "end", "resolution"],
        "mime": {
            "mp4": "video/mp4",
            "mkv": "video/x-matroska",
            "webm": "video/webm",
            "avi": "video/x-msvideo",
            "gif": "image/gif",
            "mp3": "audio/mpeg",
            "m4a": "audio/mp4",
            "wav": "audio/wav",
            "flac": "audio/flac",
            "aac": "audio/aac",
            "ogg": "audio/ogg",
        },
    },
    "archive": {
        "label": "压缩包转换",
        "tagline": "ZIP / 7Z / TAR.GZ 互转，在线查看压缩包内容",
        "exts": ["zip", "7z", "tar", "gz", "tgz", "tar.gz"],
        "targets": ["zip", "7z", "tar.gz", "list"],
        "options": ["level"],
        "mime": {
            "zip": "application/zip",
            "7z": "application/x-7z-compressed",
            "tar.gz": "application/gzip",
            "list": "application/json",
        },
    },
}

CATEGORY_ORDER = ["image", "document", "media", "archive"]

VIDEO_EXTS = {"mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v"}
AUDIO_EXTS = {"mp3", "wav", "flac", "aac", "ogg", "m4a"}
AUDIO_TARGETS = {"mp3", "m4a", "wav", "flac", "aac", "ogg"}

DOC_TARGETS = {
    "docx": ["pdf", "html", "txt"],
    "doc": ["pdf", "html", "txt"],
    "odt": ["pdf", "html", "txt"],
    "xlsx": ["pdf", "csv", "html"],
    "xls": ["pdf", "csv", "html"],
    "ods": ["pdf", "csv", "html"],
    "pptx": ["pdf"],
    "ppt": ["pdf"],
    "odp": ["pdf"],
    "md": ["html", "pdf", "txt"],
    "txt": ["pdf", "html"],
    "html": ["pdf"],
}

# 相同内容的扩展名，用于 magic 校验
EXT_EQUIV = {
    "jpg": {"jpg", "jpeg"},
    "jpeg": {"jpg", "jpeg"},
    "tif": {"tif", "tiff"},
    "tiff": {"tif", "tiff"},
    "tgz": {"tgz", "tar.gz", "gz"},
    "tar.gz": {"tgz", "tar.gz", "gz"},
    "gz": {"tgz", "tar.gz", "gz"},
    "tar": {"tar"},
}


def category_info(category: str) -> dict | None:
    return CATEGORIES.get(category)


def category_label(category: str) -> str:
    info = CATEGORIES.get(category)
    return info["label"] if info else category


def allowed_exts(category: str) -> set[str]:
    info = CATEGORIES.get(category)
    return set(info["exts"]) if info else set()


def ext_in_category(category: str, ext: str) -> bool:
    if category == "archive":
        return ext in {"zip", "7z", "tar", "gz", "tgz", "tar.gz"}
    return ext in allowed_exts(category)


def targets_for(category: str, src_ext: str) -> list[str]:
    """根据源文件扩展名给出可选目标格式。"""
    info = CATEGORIES.get(category)
    if not info:
        return []
    if category == "image":
        return list(info["targets"])
    if category == "document":
        return list(DOC_TARGETS.get(src_ext, []))
    if category == "media":
        if src_ext in AUDIO_EXTS:
            return list(AUDIO_TARGETS)
        targets = [t for t in info["targets"] if t not in AUDIO_EXTS]
        return targets + list(AUDIO_TARGETS)
    if category == "archive":
        return list(info["targets"])
    return []


def target_allowed(category: str, src_ext: str, target: str) -> bool:
    return target in targets_for(category, src_ext)


def target_mime(category: str, target: str) -> str:
    info = CATEGORIES.get(category) or {}
    return (info.get("mime") or {}).get(target, "application/octet-stream")
