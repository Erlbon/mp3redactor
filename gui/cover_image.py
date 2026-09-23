"""
Turning an image file the user picked into bytes fit to embed as an MP3
cover. ID3 players reliably display only JPEG and PNG, so those are
embedded exactly as they are (no re-encode, no quality loss); anything
else Qt can read (WebP, BMP, GIF, TIFF, ...) is converted -- to PNG if it
has transparency, else to JPEG.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtGui import QImage

from core.cover_art import sniff_mime

# The file-dialog filter: formats Qt reads out of the box.
IMAGE_FILE_FILTER = "Images (*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tif *.tiff);;All files (*)"
JPEG_QUALITY = 92


class CoverImageError(ValueError):
    """The file couldn't be read or isn't an image."""


def cover_from_bytes(data: bytes) -> tuple[bytes, str]:
    """(bytes, mime) ready to embed. Raises CoverImageError."""
    mime = sniff_mime(data)
    if mime:
        return data, mime
    image = QImage.fromData(data)
    if image.isNull():
        raise CoverImageError("Not an image format this app can read.")
    fmt, mime = ("PNG", "image/png") if image.hasAlphaChannel() else ("JPEG", "image/jpeg")
    out = QByteArray()
    buffer = QBuffer(out)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, fmt, JPEG_QUALITY if fmt == "JPEG" else -1):
        raise CoverImageError("Could not convert the image.")
    return bytes(out), mime


def cover_from_file(path: str | Path) -> tuple[bytes, str]:
    """Reads and prepares an image file. Raises CoverImageError."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise CoverImageError(f"Could not read {Path(path).name}: {exc}") from exc
    return cover_from_bytes(data)
