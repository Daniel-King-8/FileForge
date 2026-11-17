"""音视频转换：ffmpeg 容器 + `-progress pipe:1` 解析进度。"""

import re
from pathlib import Path

import config
import utils

from .base import ConvertCancelled, ConvertError, run_tool_container

VIDEO_CODECS = {
    "mp4": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"],
    "mkv": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k"],
    "webm": ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "5", "-crf", "34",
             "-b:v", "0", "-c:a", "libopus", "-b:a", "96k"],
    "avi": ["-c:v", "mpeg4", "-q:v", "5", "-c:a", "libmp3lame", "-q:a", "5"],
}

AUDIO_CODECS = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "4"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "aac": ["-c:a", "aac", "-b:a", "192k"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
}

AUDIO_TARGETS = set(AUDIO_CODECS)
_TIME_RE = re.compile(r"^(?:(\d+):)?(?:(\d{1,2}):)?(\d+(?:\.\d+)?)$")


def _to_seconds(value) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    parts = text.split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return max(0.0, seconds)


def _resolution(value) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.match(r"^\s*(\d{2,5})(?:\s*[xX*]\s*(\d{2,5}))?\s*$", str(value))
    if not match:
        return None
    width = max(16, min(7680, int(match.group(1))))
    height = max(16, min(4320, int(match.group(2)))) if match.group(2) else None
    return width, height


def _duration(task, src_name: str, log, should_cancel) -> float:
    cfg = config.cfg
    try:
        output = run_tool_container(
            cfg.ffmpeg_image,
            ["-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", src_name],
            task["id"], 60, logger=log, entrypoint="ffprobe",
            should_cancel=should_cancel, capture_all=True,
        )
    except ConvertError as exc:
        log.write(f"[media] ffprobe 获取时长失败，进度将不可用：{exc}")
        return 0.0
    for line in reversed(output.splitlines()):
        line = line.strip()
        try:
            value = float(line)
            if value > 0:
                return value
        except ValueError:
            continue
    return 0.0


def _on_progress_line(line: str, duration: float, progress) -> None:
    if duration <= 0:
        return
    seconds = None
    if line.startswith(("out_time_us=", "out_time_ms=")):
        _, _, raw = line.partition("=")
        try:
            seconds = int(raw) / 1_000_000
        except ValueError:
            seconds = None
    elif line.startswith("out_time="):
        seconds = _to_seconds(line.split("=", 1)[1])
    if seconds is not None:
        progress(5 + int(min(1.0, seconds / duration) * 90))


def convert(task, src: Path, outdir: Path, log, progress, should_cancel) -> Path:
    cfg = config.cfg
    options = utils.json_loads(task.get("options_json"))
    target = (task.get("target_format") or "").lower()
    if target not in VIDEO_CODECS and target not in AUDIO_CODECS and target != "gif":
        raise ConvertError(f"不支持的音视频目标格式：{target}")

    start = _to_seconds(options.get("start"))
    end = _to_seconds(options.get("end"))
    clip = None
    if start is not None and end is not None:
        clip = max(0.1, end - start)
    elif start is not None:
        clip = None
    elif end is not None:
        clip = max(0.1, end)

    resolution = _resolution(options.get("resolution"))
    timeout = cfg.timeouts["media"]
    out = outdir / f"result.{target}"

    progress(2)
    duration = _duration(task, src.name, log, should_cancel)
    log.write(f"[media] 时长 {duration:.2f}s，目标 {target}，片段 {start}→{end}")

    args = ["-hide_banner", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", src.name]
    if clip:
        args += ["-t", f"{clip:.3f}"]

    if target == "gif":
        fps = 10
        width = resolution[0] if resolution else 480
        vf = (f"fps={fps},scale={width}:-1:flags=lanczos,"
              "split[s0][s1];[s0]palettegen=max_colors=256[p];[s1][p]paletteuse=dither=bayer")
        args += ["-vf", vf, "-loop", "0"]
    elif target in AUDIO_TARGETS:
        args += ["-vn", *AUDIO_CODECS[target]]
    else:
        if resolution:
            width, height = resolution
            scale = f"{width}:{height}" if height else f"{width}:-2"
            if height:
                scale += ":force_original_aspect_ratio=decrease"
            args += ["-vf", f"scale={scale}"]
        args += VIDEO_CODECS[target]

    args.append(f"out/result.{target}")
    log.write(f"[media] ffmpeg -> {target}")
    progress(5)

    run_tool_container(
        cfg.ffmpeg_image, args, task["id"], timeout,
        logger=log,
        on_line=lambda line: _on_progress_line(line, duration, progress),
        should_cancel=should_cancel,
    )

    if should_cancel():
        raise ConvertCancelled("任务已取消")
    if not out.is_file() or out.stat().st_size == 0:
        raise ConvertError("ffmpeg 未生成有效输出文件")
    progress(98)
    log.write(f"[media] 完成 {out.name} {utils.human_size(out.stat().st_size)}")
    return out
