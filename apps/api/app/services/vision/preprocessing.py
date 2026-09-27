"""Image preprocessing for the counter vision pipeline (spec §7).

Validates and normalizes frames BEFORE any model sees them:
  * MIME sniffing (magic bytes — never trust the client header)
  * max byte size (COUNTER_MAX_IMAGE_MB)
  * max pixel dimensions (COUNTER_MAX_IMAGE_WIDTH/HEIGHT)
  * downscale preserving aspect ratio
  * crop extraction for detected regions
"""

from __future__ import annotations

import io
import os

from app.services.vision.providers.base import VisionProviderError
from app.services.vision.types import BBox

_ALLOWED_MAGIC = {
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG": "png",
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def validate_image(data: bytes) -> str:
    """Validate raw upload; returns the detected format ('jpeg'|'png')."""
    max_mb = _env_int("COUNTER_MAX_IMAGE_MB", 6)
    if not data:
        raise VisionProviderError("empty image", "INVALID_IMAGE")
    if len(data) > max_mb * 1024 * 1024:
        raise VisionProviderError(
            f"image exceeds {max_mb}MB limit", "IMAGE_TOO_LARGE"
        )
    for magic, fmt in _ALLOWED_MAGIC.items():
        if data.startswith(magic):
            return fmt
    raise VisionProviderError(
        "unsupported image type (JPEG/PNG only)", "INVALID_IMAGE"
    )


def decode(data: bytes):
    """Decode validated bytes to a PIL RGB image."""
    from PIL import Image

    validate_image(data)
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001
        raise VisionProviderError("corrupt image data", "INVALID_IMAGE") from exc
    return img.convert("RGB")


def clamp_dimensions(img):
    """Downscale to the configured max width/height, aspect preserved."""
    max_w = _env_int("COUNTER_MAX_IMAGE_WIDTH", 1920)
    max_h = _env_int("COUNTER_MAX_IMAGE_HEIGHT", 1920)
    w, h = img.size
    if w <= max_w and h <= max_h:
        return img
    scale = min(max_w / w, max_h / h)
    return img.resize((max(16, int(w * scale)), max(16, int(h * scale))))


def to_jpeg(img, quality: int = 88) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def crop_detection(img, bbox: BBox, padding: float = 0.06) -> bytes:
    """Extract a product crop (with a little padding) as JPEG bytes."""
    w, h = img.size
    pad_x = bbox.width * padding
    pad_y = bbox.height * padding
    left = max(0, int(bbox.x - pad_x))
    top = max(0, int(bbox.y - pad_y))
    right = min(w, int(bbox.x + bbox.width + pad_x))
    bottom = min(h, int(bbox.y + bbox.height + pad_y))
    if right - left < 8 or bottom - top < 8:
        raise VisionProviderError("detection crop too small", "INVALID_IMAGE")
    return to_jpeg(img.crop((left, top, right, bottom)))
