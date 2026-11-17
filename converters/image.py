"""图片转换：worker 内置 Pillow，无需启动容器（轻量任务）。"""

from pathlib import Path

from PIL import Image, ImageOps

import utils

from .base import ConvertCancelled, ConvertError

# worker 容器内存 512M：限制解码尺寸，避免超大图片把 worker 本身撑爆
Image.MAX_IMAGE_PIXELS = 40_000_000

FORMAT_MAP = {
    "jpg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "gif": "GIF",
    "tiff": "TIFF",
    "bmp": "BMP",
}


def _clamp_int(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _resize(im: Image.Image, width: int, height: int) -> Image.Image:
    if not width and not height:
        return im
    ow, oh = im.size
    if width and height:
        ratio = min(width / ow, height / oh)
    elif width:
        ratio = width / ow
    else:
        ratio = height / oh
    ratio = max(ratio, 0.01)
    size = (max(1, round(ow * ratio)), max(1, round(oh * ratio)))
    if size == im.size:
        return im
    return im.resize(size, Image.LANCZOS)


def _flatten(im: Image.Image) -> Image.Image:
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    if im.mode not in ("RGB", "L"):
        return im.convert("RGB")
    return im


def _prepare(im: Image.Image, target: str, width: int, height: int) -> Image.Image:
    im = ImageOps.exif_transpose(im)
    im = _resize(im, width, height)
    if target in ("jpg", "bmp"):
        return _flatten(im)
    if target == "gif":
        return im.convert("P", palette=Image.ADAPTIVE, colors=256)
    if target in ("webp", "png") and im.mode in ("CMYK", "YCbCr", "I;16"):
        return im.convert("RGB")
    return im


def convert(task, src: Path, outdir: Path, log, progress, should_cancel) -> Path:
    options = utils.json_loads(task.get("options_json"))
    target = (task.get("target_format") or "").lower()
    if target not in FORMAT_MAP:
        raise ConvertError(f"不支持的图片目标格式：{target}")

    quality = _clamp_int(options.get("quality"), 85, 1, 100)
    width = _clamp_int(options.get("width"), 0, 0, 10000)
    height = _clamp_int(options.get("height"), 0, 0, 10000)
    out = outdir / f"result.{target}"

    log.write(f"[image] Pillow 转换 -> {target} quality={quality} size={width}x{height}")
    progress(10)
    if should_cancel():
        raise ConvertCancelled("任务已取消")

    try:
        with Image.open(src) as im:
            frames = []
            durations = []
            loop = im.info.get("loop", 0)
            if target == "gif" and getattr(im, "is_animated", False):
                for index in range(min(im.n_frames, 300)):
                    if should_cancel():
                        raise ConvertCancelled("任务已取消")
                    im.seek(index)
                    durations.append(im.info.get("duration", 100))
                    frames.append(_prepare(im.copy(), target, width, height))
                save_kwargs = {
                    "format": "GIF",
                    "save_all": True,
                    "append_images": frames[1:],
                    "duration": durations,
                    "loop": loop,
                    "optimize": True,
                }
            else:
                im.seek(0)
                prepared = _prepare(im.copy(), target, width, height)
                frames = [prepared]
                save_kwargs = {"format": FORMAT_MAP[target]}
                if target == "jpg":
                    save_kwargs.update(quality=quality, optimize=True, progressive=True)
                elif target == "webp":
                    save_kwargs.update(quality=quality, method=4)
                elif target == "png":
                    save_kwargs.update(optimize=True)
                elif target == "tiff":
                    save_kwargs.update(compression="tiff_lzw")
                elif target == "gif":
                    save_kwargs.update(optimize=True)

            progress(60)
            if should_cancel():
                raise ConvertCancelled("任务已取消")
            frames[0].save(out, **save_kwargs)
    except ConvertCancelled:
        raise
    except ConvertError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, MemoryError) as exc:
        raise ConvertError(f"图片处理失败（可能分辨率过大）：{exc}") from exc

    if not out.is_file() or out.stat().st_size == 0:
        raise ConvertError("图片转换未生成有效文件")
    progress(95)
    log.write(f"[image] 完成 {out.name} {utils.human_size(out.stat().st_size)}")
    return out
