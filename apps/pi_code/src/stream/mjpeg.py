from __future__ import annotations

import io
import threading
from typing import Iterator

BOUNDARY = "frame"


class FrameBroadcaster(io.BufferedIOBase):
    """FileOutput 에 꽂는 어댑터. 인코더가 쓴 최신 JPEG 한 장만 들고, 기다리는 웹 클라이언트 모두에게 나눠준다.

    프레임을 쌓지 않으므로 느린 클라이언트는 중간 프레임을 건너뛸 뿐 메모리가 늘지 않는다.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: bytes | None = None

    def write(self, buf) -> int:
        with self._cond:
            self._frame = bytes(buf)    # V4L2 인코더는 재사용 버퍼를 넘기므로 복사해 둔다
            self._cond.notify_all()
        return len(buf)

    def wait_frame(self, timeout: float = 5.0) -> bytes | None:
        """다음 프레임까지 기다린다. timeout 동안 안 오면 None (카메라가 멈춘 것)."""
        with self._cond:
            if not self._cond.wait(timeout):
                return None
            return self._frame


def multipart_frames(source: FrameBroadcaster) -> Iterator[bytes]:
    """multipart/x-mixed-replace 본문. 브라우저 <img> 가 이걸 받아 영상처럼 갱신한다."""
    while True:
        frame = source.wait_frame()
        if frame is None:
            return
        yield (f"--{BOUNDARY}\r\n"
               f"Content-Type: image/jpeg\r\n"
               f"Content-Length: {len(frame)}\r\n\r\n").encode() + frame + b"\r\n"
