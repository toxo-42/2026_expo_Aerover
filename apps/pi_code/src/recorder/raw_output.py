from __future__ import annotations

import os

from picamera2.outputs import Output


class SafeRawOutput(Output):
    """H.264 생스트림 + 프레임 타임스탬프를 버퍼링 없이 기록.

    전원이 갑자기 끊겨도 sync_every 프레임 전까지만 잃도록 주기적으로 fsync 한다.
    """

    def __init__(self, video_path: str, pts_path: str, sync_every: int, gap_ms: int) -> None:
        super().__init__()
        self._vid = open(video_path, "wb", buffering=0)   # 파이썬 버퍼 우회
        self._pts = open(pts_path, "w")
        self._pts.write("# timecode format v2\n")
        self._sync_every = sync_every
        self._gap_ms = gap_ms
        self._t0: int | None = None
        self._last_ms: float | None = None
        self.frames = 0
        self.gaps: list[tuple[int, float]] = []     # [(프레임번호, 간격ms), ...]

    def outputframe(self, frame, keyframe=True, timestamp=None, *a, **kw):
        self._vid.write(frame)

        if timestamp is not None:
            if self._t0 is None:
                self._t0 = timestamp
            ms = (timestamp - self._t0) / 1000.0
            if self._last_ms is not None and (ms - self._last_ms) > self._gap_ms:
                self.gaps.append((self.frames, round(ms - self._last_ms, 1)))
            self._last_ms = ms
            self._pts.write(f"{ms:.3f}\n")

        self.frames += 1
        if self.frames % self._sync_every == 0:
            self._flush()

    def _flush(self) -> None:
        self._pts.flush()
        os.fsync(self._vid.fileno())
        os.fsync(self._pts.fileno())

    def close(self) -> None:
        try:
            self._flush()
        finally:
            self._vid.close()
            self._pts.close()
