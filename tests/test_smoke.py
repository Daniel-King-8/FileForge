"""本地冒烟测试（无需 Docker）。

覆盖：页面渲染、注册登录、上传校验、图片转换、队列 worker、下载头、
SSE、分享、管理员接口、清理任务。

运行：
    python tests/test_smoke.py
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="fileforge-test-"))
os.environ.update({
    "DATA_DIR": str(WORKDIR / "data"),
    "UPLOADS_DIR": str(WORKDIR / "uploads"),
    "TMP_DIR": str(WORKDIR / "tmp"),
    "RESULTS_DIR": str(WORKDIR / "results"),
    "LOGS_DIR": str(WORKDIR / "logs"),
    "SECRET_KEY": "test-secret-key",
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "AdminTest123",
    "MAX_UPLOAD_MB": "5",
    "USER_QUOTA_MB": "20",
    "RESULT_TTL_HOURS": "24",
    "MAX_CONCURRENT": "2",
})

from PIL import Image  # noqa: E402

import app as app_module  # noqa: E402
import cleanup  # noqa: E402
import config  # noqa: E402

PASSED = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} 失败 {detail}")
    PASSED.append(name)
    print(f"  [ok] {name}")


def make_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (320, 200), (245, 158, 11, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def main() -> None:
    cfg = config.cfg
    cfg.ensure_dirs()
    application = app_module.app
    client = application.test_client()

    print("1. 页面渲染")
    for path in ("/", "/login", "/register", "/convert/image", "/convert/document",
                 "/convert/media", "/convert/archive"):
        response = client.get(path)
        check(f"GET {path}", response.status_code == 200, f"status={response.status_code}")

    print("2. 注册 / 登录")
    response = client.post("/api/register", json={
        "username": "alice", "email": "alice@example.com", "password": "Passw0rd!",
    })
    check("注册成功", response.status_code == 201, response.get_data(as_text=True))
    response = client.get("/api/me")
    check("会话已登录", response.get_json()["user"]["username"] == "alice")
    response = client.post("/api/register", json={
        "username": "alice", "email": "other@example.com", "password": "Passw0rd!",
    })
    check("重复用户名被拒绝", response.status_code == 409)
    response = client.post("/api/login", json={"username": "alice", "password": "wrong"})
    check("错误密码被拒绝", response.status_code == 401)

    print("3. 登录后页面")
    for path in ("/tasks", "/profile"):
        response = client.get(path)
        check(f"GET {path}", response.status_code == 200, f"status={response.status_code}")

    print("4. 上传校验")
    png = make_png()
    response = client.post("/api/upload", data={
        "category": "image",
        "file": (io.BytesIO(png), "photo.png"),
    }, content_type="multipart/form-data")
    check("上传 PNG", response.status_code == 201, response.get_data(as_text=True))
    task_id = response.get_json()["task"]["id"]

    response = client.post("/api/upload", data={
        "category": "image",
        "file": (io.BytesIO(b"this is definitely not a png"), "fake.png"),
    }, content_type="multipart/form-data")
    check("伪造后缀被拒绝", response.status_code == 415, response.get_data(as_text=True))

    response = client.post("/api/upload", data={
        "category": "image",
        "file": (io.BytesIO(png), "photo.zip"),
    }, content_type="multipart/form-data")
    check("扩展名不在白名单被拒绝", response.status_code == 415)

    print("5. 提交转换")
    response = client.post(f"/api/tasks/{task_id}/convert", json={
        "target_format": "webp", "options": {"quality": 80, "width": 160},
    })
    check("提交 webp 转换", response.status_code == 200, response.get_data(as_text=True))
    response = client.post(f"/api/tasks/{task_id}/convert", json={"target_format": "exe"})
    check("非法目标格式被拒绝", response.status_code == 400)

    print("6. worker 消费队列")
    env = os.environ.copy()
    env["WORKER_ONCE"] = "1"
    proc = subprocess.run(
        [sys.executable, "worker.py"], cwd=str(ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
    check("worker 正常退出", proc.returncode == 0)

    task = None
    for _ in range(30):
        response = client.get(f"/api/tasks/{task_id}")
        task = response.get_json()["task"]
        if task["status"] in ("done", "failed"):
            break
        time.sleep(0.3)
    check("任务完成", task and task["status"] == "done", str(task and task.get("error_msg")))
    check("结果大小 > 0", (task.get("result_size") or 0) > 0)

    print("7. SSE / 日志")
    response = client.get(f"/api/tasks/{task_id}/stream")
    body = response.get_data(as_text=True)
    check("SSE 返回终态", '"status": "done"' in body, body[:200])
    response = client.get(f"/api/tasks/{task_id}/log")
    check("日志可读取", response.status_code == 200 and "fileforge" in response.get_data(as_text=True))

    print("8. 详情 / 下载")
    response = client.get(f"/tasks/{task_id}")
    check("任务详情页", response.status_code == 200, f"status={response.status_code}")
    response = client.get(f"/download/{task_id}")
    check("下载走 X-Accel", response.status_code == 200 and
          response.headers.get("X-Accel-Redirect", "").startswith("/_files/"),
          str(dict(response.headers)))

    print("9. 分享")
    response = client.post(f"/api/tasks/{task_id}/share", json={
        "expire_hours": 1, "max_downloads": 2,
    })
    check("创建分享", response.status_code == 200, response.get_data(as_text=True))
    token = response.get_json()["token"]
    response = client.get(f"/share/{token}")
    check("分享页可访问", response.status_code == 200)
    response = client.get(f"/share/{token}/download")
    check("分享下载", response.status_code == 200 and "X-Accel-Redirect" in response.headers)

    print("10. 任务列表 / 批量")
    response = client.get("/api/tasks?status=done")
    check("任务列表筛选", response.status_code == 200 and len(response.get_json()["items"]) == 1)
    response = client.get("/tasks?status=done")
    check("任务列表页", response.status_code == 200)
    response = client.post("/api/tasks/batch/download", json={"ids": [task_id]})
    check("批量打包下载", response.status_code == 200, response.get_data(as_text=True))
    batch_url = response.get_json()["url"]
    response = client.get(batch_url)
    check("批量包下载头", response.status_code == 200 and "X-Accel-Redirect" in response.headers)

    print("11. 管理员")
    admin = application.test_client()
    response = admin.post("/api/login", json={"username": "admin", "password": "AdminTest123"})
    check("管理员登录", response.status_code == 200, response.get_data(as_text=True))
    for path in ("/admin", "/api/admin/stats", "/api/admin/users",
                 "/api/admin/alerts", "/api/admin/tasks", "/api/admin/audit"):
        response = admin.get(path)
        check(f"GET {path}", response.status_code == 200, f"status={response.status_code}")
    response = admin.post("/api/admin/alerts/test")
    check("写入测试告警", response.status_code == 200)
    response = admin.get("/api/admin/alerts?unacked=1")
    check("告警列表", response.status_code == 200 and len(response.get_json()["items"]) >= 1)
    response = client.get("/admin")
    check("普通用户访问后台被拒绝", response.status_code == 403)

    print("12. 转换器 / 安全单元检查")
    import struct

    import sniff
    from converters import archive as archive_converter
    from converters import document as document_converter
    from converters.base import ConvertError

    class LogStub:
        def __init__(self):
            self.lines = []

        def write(self, message):
            self.lines.append(str(message))

    unit = WORKDIR / "unit"
    outdir = unit / "out"
    outdir.mkdir(parents=True, exist_ok=True)
    md_path = unit / "input.md"
    md_path.write_text("# 标题\n\n这是 **加粗** 与 `代码`。\n", encoding="utf-8")

    doc_task = {"id": "unit-doc", "target_format": "html",
                "src_name": "测试.md", "options_json": "{}"}
    parsed = document_converter.convert(doc_task, md_path, outdir, LogStub(), lambda p: None, lambda: False)
    check("Markdown -> HTML", parsed.exists() and "<h1" in parsed.read_text(encoding="utf-8"))

    doc_task_txt = dict(doc_task, target_format="txt")
    parsed = document_converter.convert(doc_task_txt, md_path, outdir, LogStub(), lambda p: None, lambda: False)
    check("Markdown -> TXT", parsed.exists() and "标题" in parsed.read_text(encoding="utf-8"))

    zipped = b"PK\x03\x04" + b"\x00" * 22 + struct.pack("<HH", 17, 0) + b"word/document.xml"
    check("ZIP 内 DOCX 识别", sniff.detect(zipped)[1] == "docx")
    check("MP4 识别", sniff.detect(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)[1] == "mp4")
    check("伪造图片识别为文本", sniff.detect(b"hello world")[1] == "text")
    check("普通 ZIP 不能冒充 docx", not sniff.kind_compatible("docx", "zip"))
    check("DOCX 扩展名匹配", sniff.kind_compatible("docx", "docx"))

    def expect_convert_error(label: str, entries: list) -> None:
        try:
            archive_converter._check_entries(entries, LogStub())
        except ConvertError:
            check(label, True)
        else:
            check(label, False, "未抛出 ConvertError")

    expect_convert_error("路径穿越被拒绝", [{
        "path": "../etc/passwd", "size": 10, "folder": False, "attrs": "A", "encrypted": False,
    }])
    expect_convert_error("zip 炸弹被拒绝", [{
        "path": "big.bin", "size": 8 * 1024 ** 3, "folder": False, "attrs": "A", "encrypted": False,
    }])
    expect_convert_error("符号链接被拒绝", [{
        "path": "link", "size": 1, "folder": False, "attrs": "lrwxrwxrwx", "encrypted": False,
    }])
    archive_converter._check_entries([{
        "path": "a/b.txt", "size": 10, "folder": False, "attrs": "A", "encrypted": False,
    }], LogStub())
    check("正常压缩包条目通过", True)

    print("13. Docker 命令构建")
    from unittest.mock import patch

    import converters.base as base_module

    class FakeProc:
        def __init__(self, returncode=0, lines=()):
            self.returncode = returncode
            self.stdout = iter(list(lines))

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            pass

    calls = []

    def fake_popen(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return FakeProc(125, ["invalid mount config for type volume: field volume-subpath unsupported"])
        return FakeProc(0, ["conversion ok"])

    with patch.object(base_module, "ensure_image_ready", lambda image: None), \
            patch.object(base_module.subprocess, "Popen", side_effect=fake_popen):
        base_module.run_tool_container(
            "fileforge/media-tools:1.0.0", ["7z", "x", "input.zip"],
            "task12345678", 30, subdir="extract",
        )

    first, second = calls[0], calls[1]
    joined = " ".join(first)
    check("转换容器断网", "--network none" in joined)
    check("资源限制参数", "--memory 1g" in joined and "--cpus 1" in joined and "--pids-limit 64" in joined)
    check("非 root 运行", "--user 1000:1000" in joined)
    check("按任务子路径挂载", "volume-subpath=task12345678" in joined)
    check("子目录工作路径", "/work/extract" in joined and "-w" in first)
    check("不支持 subpath 时自动降级", len(calls) == 2 and "volume-subpath" not in " ".join(second)
          and "/work/task12345678/extract" in " ".join(second))

    print("14. cleanup")
    summary = cleanup.run_once()
    check("cleanup 正常执行", isinstance(summary, dict) and "expired" in summary)

    print("15. 删除任务")
    response = client.delete(f"/api/tasks/{task_id}")
    check("删除任务", response.status_code == 200, response.get_data(as_text=True))
    check("删除后详情 404", client.get(f"/api/tasks/{task_id}").status_code == 404)
    check("删除后下载 404", client.get(f"/download/{task_id}").status_code == 404)
    check("删除后分享 404", client.get(f"/share/{token}").status_code == 404)
    response = client.get("/api/me")
    check("配额已释放", response.get_json()["user"]["used_bytes"] == 0,
          str(response.get_json()["user"]["used_bytes"]))

    print(f"\n全部通过：{len(PASSED)} 项检查")
    print(f"测试数据目录：{WORKDIR}")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
