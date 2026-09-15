"""YOLO 탐지 — 연결점과 워커.

**`best.pt` 가 아직 없다.** 그래서 실제 추론은 비어 있고, 지금 확정하는 것은
모델이 왔을 때 값만 흘려넣을 자리다:

    detect(frame) -> list[(cls, conf, x1, y1, x2, y2)]      # 콘티 4절

팀 저장소 `run_detect.py` 의 `detect()` 는 **이미지 파일 경로**를 받는다.
실시간에는 경로가 없으므로 여기서는 **프레임(QImage)** 을 받는다. 반환 형식은
같으니, 팀원 코드를 옮길 때 바꿀 곳은 `QImage → ndarray` 변환 한 줄이다.

박스 좌표는 **원본 프레임 픽셀 기준**이다. 화면 축소는 `ui/video.py` 가 한다.

워커는 탐지기를 주입받는다 (`Detector`). 기본값이 `detect` 라 화면 코드는 그대로 두고,
테스트나 다른 모델은 호출 가능한 객체 하나만 넘기면 된다.
"""
from __future__ import annotations

import threading
from typing import Protocol

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from src.config import DETECT_MODEL_PATH

# (클래스명, 신뢰도, x1, y1, x2, y2)
Box = tuple[str, float, int, int, int, int]


class Detector(Protocol):
    def __call__(self, frame: QImage) -> list[Box]: ...


def model_available() -> bool:
    return DETECT_MODEL_PATH.exists()


def detect(frame: QImage) -> list[Box]:
    """★ best.pt 가 오면 여기만 채운다. ★

    호출 전에 `model_available()` 로 거르므로 모델 없이 불릴 일은 없다.
    """
    raise NotImplementedError(f"모델이 없다 — {DETECT_MODEL_PATH}")


class DetectWorker(QThread):
    """프레임 하나를 추론하는 동안 들어온 프레임은 **버린다.**

    GUI 스레드에서 추론하면 프레임마다 화면이 얼어붙는다. 그렇다고 큐에 쌓으면
    추론이 링크보다 느릴 때 지연이 무한히 늘어난다. 실시간 화면에서 중요한 것은
    빠짐없이 보는 게 아니라 **지금 것을 보는 것**이라, 최신 한 장만 남긴다.
    """

    result = Signal(list)       # list[Box] — 신뢰도 필터를 통과한 것만
    failed = Signal(str)

    WAKE_SEC = 0.1              # 프레임이 없어도 이 주기로 깨어나 중단 여부를 본다

    def __init__(self, conf: float, detector: Detector = detect, parent=None) -> None:
        super().__init__(parent)
        self.conf = conf        # 화면 슬라이더가 도중에 바꾼다
        self._detector = detector
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._pending: QImage | None = None

    def submit(self, frame: QImage) -> None:
        with self._lock:
            self._pending = frame       # 아직 못 본 프레임은 덮어쓴다
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.WAKE_SEC)
            self._wake.clear()
            frame = self._take()
            if frame is None:
                continue
            try:
                boxes = self._detector(frame)
            except Exception as e:
                self.failed.emit(f"탐지 중단 — {e}")
                return
            self.result.emit([b for b in boxes if b[1] >= self.conf])

    def _take(self) -> QImage | None:
        with self._lock:
            frame, self._pending = self._pending, None
        return frame
