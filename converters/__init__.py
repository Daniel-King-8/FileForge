"""转换器注册表。"""

from . import archive, document, image, media

CONVERTERS = {
    "image": image.convert,
    "document": document.convert,
    "media": media.convert,
    "archive": archive.convert,
}

__all__ = ["CONVERTERS"]
