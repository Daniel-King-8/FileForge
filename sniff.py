"""文件真实类型识别。

策略：
1. 读文件头 4KB，用 python-magic（libmagic）判断 MIME；
2. 同时用内置魔数解析（不依赖系统库，本地开发也能跑）；
3. 容器格式（ZIP/OLE）再下钻一层，识别 docx/xlsx/pptx/odt 等。

返回 (mime, kind)。kind 可能是具体扩展名，也可能是家族标记：
  ole  —— 老版 Office（doc/xls/ppt 无法仅凭文件头区分）
  text —— 纯文本（txt/md/html 之一）
  zip  —— 普通 zip
"""

import os
import re
import sys

# python-magic 依赖系统 libmagic：
# - 容器（Debian）内已安装 libmagic1，默认启用；
# - Windows 开发机常常缺少/装错 DLL，ctypes 加载会直接崩溃（无法 try/except），
#   因此非 Linux 平台默认关闭，仅靠内置魔数解析；可用 LIBMAGIC=on/off 强制。
_force = os.environ.get("LIBMAGIC", "auto").strip().lower()
if _force == "on":
    _MAGIC_ENABLED = True
elif _force == "off":
    _MAGIC_ENABLED = False
else:
    _MAGIC_ENABLED = sys.platform.startswith("linux")

MAGIC_MAP = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/tiff": "tiff",
    "image/bmp": "bmp",
    "image/x-ms-bmp": "bmp",
    "application/zip": "zip",
    "application/x-7z-compressed": "7z",
    "application/gzip": "gz",
    "application/x-gzip": "gz",
    "application/x-tar": "tar",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "video/mp4": "mp4",
    "video/x-matroska": "mkv",
    "video/webm": "webm",
    "video/x-msvideo": "avi",
    "video/quicktime": "mov",
    "video/x-flv": "flv",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
    "audio/aac": "aac",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "text/html": "html",
    "text/xml": "html",
    "application/xhtml+xml": "html",
}

OLE_COMPATIBLE = {"doc", "xls", "ppt"}
# 普通 ZIP 只允许声明为 zip；OOXML 由魔数/ libmagic 细分后再比对
OOXML_COMPATIBLE = {"zip"}
TEXT_COMPATIBLE = {"txt", "md", "html", "csv", "log"}

_HTML_RE = re.compile(rb"^\s*(<!doctype\s+html|<html[\s>])", re.IGNORECASE)


def _magic_mime(head: bytes) -> str | None:
    if not _MAGIC_ENABLED:
        return None
    try:
        import magic  # type: ignore

        return magic.from_buffer(head, mime=True)
    except Exception:
        return None


def _zip_kind(head: bytes) -> str:
    """根据 ZIP 第一个本地文件头 / 内容特征判断子类型。"""
    try:
        if head[0:4] == b"PK\x03\x04":
            name_len = int.from_bytes(head[26:28], "little")
            extra_len = int.from_bytes(head[28:30], "little")
            name = head[30:30 + name_len].decode("utf-8", "ignore").lower()
            if name == "mimetype":
                body = head[30 + name_len + extra_len:30 + name_len + extra_len + 120]
                if b"opendocument.text" in body:
                    return "odt"
                if b"opendocument.spreadsheet" in body:
                    return "ods"
                if b"opendocument.presentation" in body:
                    return "odp"
    except (IndexError, ValueError):
        pass
    sample = head[:4096].lower()
    if b"word/document" in sample or b"word/" in sample:
        return "docx"
    if b"xl/workbook" in sample or b"xl/" in sample:
        return "xlsx"
    if b"ppt/presentation" in sample or b"ppt/" in sample:
        return "pptx"
    return "zip"


def _signature(head: bytes) -> tuple[str | None, str]:
    """返回 (mime, kind)。"""
    if not head:
        return None, "empty"

    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", "webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff", "tiff"
    if head[:2] == b"BM":
        return "image/bmp", "bmp"
    if head.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    if head[:4] == b"PK\x03\x04":
        return "application/zip", _zip_kind(head)
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "application/x-7z-compressed", "7z"
    if head[:2] == b"\x1f\x8b":
        return "application/gzip", "gz"
    if len(head) > 262 and head[257:262] == b"ustar":
        return "application/x-tar", "tar"
    if head[:4] == b"\xd0\xcf\x11\xe0":
        return "application/x-ole-storage", "ole"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return "video/quicktime", "mov"
        if brand in (b"M4A ", b"M4B "):
            return "audio/mp4", "m4a"
        if brand in (b"M4V ",):
            return "video/mp4", "m4v"
        return "video/mp4", "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return ("video/webm", "webm") if b"webm" in head[:4096].lower() else ("video/x-matroska", "mkv")
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video/x-msvideo", "avi"
    if head[:3] == b"FLV":
        return "video/x-flv", "flv"
    if head[:4] == b"0&\xb2u":
        return "video/x-ms-wmv", "wmv"
    if head[:4] == b"OggS":
        return "audio/ogg", "ogg"
    if head[:4] == b"fLaC":
        return "audio/flac", "flac"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav", "wav"
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg", "mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0:
        return "audio/aac", "aac"
    if _HTML_RE.match(head):
        return "text/html", "html"

    # 纯文本判断：无 NUL 字节，并能按 UTF-8 / GB18030 解码
    if b"\x00" not in head:
        for encoding in ("utf-8", "gb18030"):
            try:
                head.decode(encoding)
                return "text/plain", "text"
            except UnicodeDecodeError:
                continue
    return None, "unknown"


def detect(head: bytes) -> tuple[str | None, str]:
    """魔数优先；ZIP/OLE/文本等容器再借助 libmagic 细分。"""
    mime, kind = _signature(head)
    magic_mime = _magic_mime(head)
    if magic_mime:
        magic_kind = MAGIC_MAP.get(magic_mime)
        if magic_kind:
            if kind in {"zip", "ole", "text", "gz", "unknown", "empty"}:
                # 仅当内置识别不够具体时才采用 libmagic 结果
                if not (kind == "text" and magic_kind == "txt"):
                    kind = magic_kind
                mime = magic_mime
            elif magic_kind == kind:
                mime = magic_mime
    return mime, kind


def kind_compatible(claimed_ext: str, kind: str) -> bool:
    """上传声明扩展名是否与真实类型一致。"""
    claimed = (claimed_ext or "").lower()
    if claimed == kind:
        return True
    if kind == "ole":
        return claimed in OLE_COMPATIBLE
    if kind in {"docx", "xlsx", "pptx", "odt", "ods", "odp"}:
        return claimed == kind
    if kind == "zip":
        return claimed in OOXML_COMPATIBLE
    if kind == "text":
        return claimed in TEXT_COMPATIBLE
    if kind == "gz":
        return claimed in {"gz", "tar.gz", "tgz"}
    if kind == "jpg":
        return claimed == "jpeg"
    if kind == "tiff":
        return claimed == "tif"
    if kind == "m4a":
        return claimed in {"m4a", "aac"}
    if kind == "mp4":
        return claimed in {"mp4", "m4v"}
    if kind == "m4v":
        return claimed in {"m4v", "mp4"}
    return False
