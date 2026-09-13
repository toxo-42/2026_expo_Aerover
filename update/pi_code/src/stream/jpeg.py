from __future__ import annotations

import io

from PIL import Image


def encode_bgr_as_jpeg(frame, quality: int) -> bytes:
    """picamera2 의 RGB888 배열(실제 채널 순서는 BGR) → JPEG 바이트."""
    img = Image.fromarray(frame[:, :, ::-1])     # BGR → RGB (피부색 보정)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()
