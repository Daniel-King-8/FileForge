"""文档转换：LibreOffice 容器 + Markdown 由 worker 本地处理。"""

import re
from pathlib import Path

import config
import utils

from .base import ConvertError, run_tool_container

FILTERS = {
    "pdf": "pdf",
    "csv": "csv:Text - txt - csv (StarCalc):44,34,76",
    "html": "html",
    "txt": "txt:Text",
}

MD_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{title}</title>
<style>
body {{ font-family: "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; line-height: 1.7;
       max-width: 820px; margin: 40px auto; padding: 0 24px; color: #1f2328; }}
h1,h2,h3 {{ line-height: 1.3; margin-top: 1.4em; }}
pre {{ background: #f6f8fa; padding: 12px; border-radius: 8px; overflow-x: auto; }}
code {{ background: #f6f8fa; padding: .15em .35em; border-radius: 4px; }}
blockquote {{ margin: 0; padding: .2em 1em; color: #59636e; border-left: 4px solid #d0d7de; }}
table {{ border-collapse: collapse; }} th, td {{ border: 1px solid #d0d7de; padding: 6px 10px; }}
img {{ max-width: 100%; }}
</style></head><body>
{body}
</body></html>
"""


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _md_to_html(src: Path, dest: Path) -> Path:
    import markdown

    text = _read_text(src)
    body = markdown.markdown(
        text,
        extensions=["extra", "tables", "fenced_code", "codehilite", "toc", "sane_lists"],
        output_format="html5",
    )
    title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else src.stem
    dest.write_text(MD_HTML_TEMPLATE.format(title=title, body=body), encoding="utf-8")
    return dest


def _soffice(task, input_rel: str, outdir: Path, target: str, log, progress,
             should_cancel, timeout: int) -> Path:
    cfg = config.cfg
    filter_name = FILTERS.get(target)
    if not filter_name:
        raise ConvertError(f"LibreOffice 不支持的目标：{target}")

    progress(25)
    cmd = [
        "soffice",
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        "-env:UserInstallation=file:///tmp/lo",
        "--convert-to", filter_name,
        "--outdir", "out",
        input_rel,
    ]
    log.write(f"[document] LibreOffice -> {target}")
    run_tool_container(
        cfg.office_image, cmd, task["id"], timeout,
        logger=log, should_cancel=should_cancel,
    )
    progress(90)

    suffix = f".{target}"
    produced = [
        p for p in outdir.iterdir()
        if p.is_file() and p.stat().st_size > 0 and p.suffix.lower() == suffix
    ]
    if not produced:
        raise ConvertError("LibreOffice 未生成输出文件（可能格式不受支持或文件损坏）")
    produced.sort(key=lambda p: p.stat().st_size, reverse=True)
    if len(produced) > 1:
        log.write(f"[document] 生成多个文件，取最大的 {produced[0].name}")
    return produced[0]


def convert(task, src: Path, outdir: Path, log, progress, should_cancel) -> Path:
    cfg = config.cfg
    target = (task.get("target_format") or "").lower()
    ext = utils.filename_ext(task["src_name"])
    timeout = cfg.timeouts["document"]
    out = outdir / f"result.{target}"

    def finish(produced: Path) -> Path:
        if produced.resolve() != out.resolve():
            if out.exists():
                out.unlink()
            produced.rename(out)
        if not out.is_file() or out.stat().st_size == 0:
            raise ConvertError("文档转换未生成有效文件")
        return out

    if ext == "md":
        if target == "txt":
            log.write("[document] Markdown -> 纯文本")
            progress(60)
            out.write_text(_read_text(src), encoding="utf-8")
            progress(95)
            return out
        html_path = outdir / "input.html"
        if target == "html":
            log.write("[document] Markdown -> HTML")
            progress(60)
            _md_to_html(src, html_path)
            progress(95)
            return finish(html_path)
        if target == "pdf":
            _md_to_html(src, html_path)
            progress(50)
            produced = _soffice(task, "out/input.html", outdir, "pdf", log, progress,
                                should_cancel, timeout)
            return finish(produced)
        raise ConvertError(f"Markdown 不支持转换为 {target}")

    if ext == "txt" and target == "html":
        log.write("[document] 纯文本 -> HTML（转义后包装）")
        import html as html_lib

        body = "".join(f"<p>{html_lib.escape(line)}</p>\n" for line in _read_text(src).splitlines())
        out.write_text(MD_HTML_TEMPLATE.format(title=src.stem, body=body), encoding="utf-8")
        progress(95)
        return out

    if target not in FILTERS:
        raise ConvertError(f"文档不支持转换为 {target}")
    if ext in ("xlsx", "xls", "ods") and target == "csv":
        log.write("[document] 注意：CSV 只导出第一个工作表")

    produced = _soffice(task, src.name, outdir, target, log, progress, should_cancel, timeout)
    return finish(produced)
