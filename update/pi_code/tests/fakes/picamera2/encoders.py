from __future__ import annotations

import threading
import time


class Encoder:
    """출력에 붙으면 백그라운드에서 프레임을 밀어 넣는다 — 진짜 인코더의 스레드를 흉내 낸다."""

    frame = b"\xff\xd8fake-jpeg\xff\xd9"
    period = 0.005

    def __init__(self, **kw) -> None:
        self.kw = kw
        self.output = None
        self._thread: threading.Thread | None = None
        self._run = threading.Event()

    def attach(self, output) -> None:
        self.output = output
        self._run.set()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def detach(self) -> None:
        self._run.clear()
        if self._thread is not None:
            self._thread.join(1.0)

    def _pump(self) -> None:
        ts = 0
        while self._run.is_set():
            self.output.outputframe(self.frame, keyframe=True, timestamp=ts)
            ts += 33_000
            time.sleep(self.period)


class JpegEncoder(Encoder):
    pass


class MJPEGEncoder(Encoder):
    pass


class H264Encoder(Encoder):
    def __init__(self, bitrate=None, repeat=False, iperiod=None, **kw) -> None:
        super().__init__(bitrate=bitrate, repeat=repeat, iperiod=iperiod, **kw)
