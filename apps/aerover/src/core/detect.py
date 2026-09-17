"""YOLO 탐지 — 연결점과 워커.

    detect(frame) -> list[(cls, conf, x1, y1, x2, y2)]      # 콘티 4절

**영상은 노트북 카메라가 아니라 파이에서 온다.** 이 모듈은 카메라를 열지 않는다 —
프레임은 `core/link.py` 의 RTP/JPEG 수신이 만들어 `LinkHub` → `pages/detect.py` →
`DetectWorker.submit()` 으로 들어온다. `camtest.py` 의 `cv2.VideoCapture` 자리를
그 링크가 대신하는 셈이고, 여기로 옮겨온 것은 **추론과 클래스 통합**뿐이다.

박스 좌표는 **원본 프레임 픽셀 기준**이다. 화면 축소는 `ui/video.py` 가 한다.

워커는 탐지기를 주입받는다 (`Detector`). 기본값이 `detect` 라 화면 코드는 그대로 두고,
테스트나 다른 모델은 호출 가능한 객체 하나만 넘기면 된다.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Protocol

import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from src.config import DETECT_IMGSZ, DETECT_MODEL_PATH

# (클래스명, 신뢰도, x1, y1, x2, y2)
Box = tuple[str, float, int, int, int, int]

# 모델에는 슬라이더 최소값으로 물어보고, 화면 임계는 워커가 건다.
# 그래야 슬라이더를 올렸다 내려도 다시 추론하지 않는다 (`DetectWorker.conf`).
CONF_FLOOR = 0.05

# 사전학습(COCO) 클래스를 person / vehicle 로 통합한다 — `camtest.py` 의 KEEP 과 같은 표.
# 표에 없는 클래스는 버린다. **팀 학습본(best.pt)에는 적용하지 않는다** (아래 `_is_coco`).
COCO_KEEP: dict[str, str] = {
    "person": "person",
    "car": "vehicle", "bus": "vehicle", "truck": "vehicle",
    "train": "vehicle", "motorcycle": "vehicle", "bicycle": "vehicle",
    "boat": "vehicle", "airplane": "vehicle",   # 모형 구급차·밴이 이렇게 잡히는 경우가 있다
}


class Detector(Protocol):
    def __call__(self, frame: QImage) -> list[Box]: ...


def model_available() -> bool:
    return DETECT_MODEL_PATH.exists()


def qimage_to_bgr(frame: QImage) -> np.ndarray:
    """QImage → (H, W, 3) uint8 BGR.

    ultralytics 는 ndarray 입력을 **cv2 규약(BGR)** 으로 본다. 한 줄의 바이트 수
    (`bytesPerLine`)는 폭*3 보다 클 수 있어 (정렬 패딩) 잘라내고 쓴다.
    """
    img = frame.convertToFormat(QImage.Format.Format_RGB888)
    h, w, stride = img.height(), img.width(), img.bytesPerLine()
    flat = np.frombuffer(img.constBits(), dtype=np.uint8, count=h * stride)
    rgb = flat.reshape(h, stride)[:, :w * 3].reshape(h, w, 3)
    return rgb[:, :, ::-1].copy()       # 복사해야 연속 메모리가 된다 (torch 가 요구한다)


def is_coco(names) -> bool:
    """사전학습 모델인가. person·car 가 둘 다 있으면 COCO 로 본다."""
    return {"person", "car"} <= set(names.values() if hasattr(names, "values") else names)


class YoloDetector:
    """가중치 하나를 들고 프레임마다 추론한다.

    **모델은 처음 쓸 때 연다** (`load`). ultralytics·torch 를 import 하는 데 몇 초가
    걸려서, 앱이 뜨는 길목에서 하면 창이 그동안 멎는다. 워커가 자기 스레드에서
    `load()` 를 먼저 부르므로 GUI 는 영향을 받지 않는다.
    """

    def __init__(self, weights: Path = DETECT_MODEL_PATH, imgsz: int = DETECT_IMGSZ,
                 conf: float = CONF_FLOOR, coco: bool | None = None) -> None:
        self.weights = weights
        self.imgsz = imgsz
        self.conf = conf
        # None 이면 모델을 열 때 클래스 이름을 보고 정한다 (`is_coco`).
        self.coco = coco
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        """모델을 열고 돌려준다. 여러 번 불러도 한 번만 연다."""
        with self._lock:
            if self._model is None:
                if not self.weights.exists():
                    raise FileNotFoundError(f"YOLO 가중치가 없다 — {self.weights}")
                from ultralytics import YOLO       # 지연 import — 없어도 앱은 뜬다
                model = YOLO(str(self.weights))
                if self.coco is None:
                    self.coco = is_coco(model.names)
                self._model = model
            return self._model

    def label(self, raw: str) -> str | None:
        """화면에 쓸 이름. 버릴 클래스면 None."""
        return COCO_KEEP.get(raw) if self.coco else raw

    def __call__(self, frame: QImage) -> list[Box]:
        model = self.load()
        result = model.predict(qimage_to_bgr(frame), conf=self.conf, imgsz=self.imgsz,
                               verbose=False)[0]
        boxes: list[Box] = []
        for b in result.boxes:
            name = self.label(result.names[int(b.cls)])
            if name is None:
                continue
            x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
            boxes.append((name, float(b.conf), x1, y1, x2, y2))
        return boxes


# 앱 전체가 쓰는 탐지기 하나. 모델을 프레임마다 다시 열지 않기 위해서다.
_default = YoloDetector()


def detect(frame: QImage) -> list[Box]:
    """기본 탐지기. 화면은 `DetectWorker` 를 통해 이것을 쓴다."""
    return _default(frame)


class DetectWorker(QThread):
    """프레임 하나를 추론하는 동안 들어온 프레임은 **버린다.**

    GUI 스레드에서 추론하면 프레임마다 화면이 얼어붙는다. 그렇다고 큐에 쌓으면
    추론이 링크보다 느릴 때 지연이 무한히 늘어난다. 실시간 화면에서 중요한 것은
    빠짐없이 보는 게 아니라 **지금 것을 보는 것**이라, 최신 한 장만 남긴다.
    """

    ready = Signal()            # 모델을 열었다 — 이때부터 프레임을 먹는다
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
        if not self._load():
            return
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

    def _load(self) -> bool:
        """탐지기가 미리 열 것이 있으면 여기서 연다 (모델 로딩은 몇 초가 걸린다).
        `load` 가 없는 탐지기(테스트의 함수 하나)도 그대로 쓴다."""
        load = getattr(self._detector, "load", None)
        if load is not None:
            try:
                load()
            except Exception as e:
                self.failed.emit(f"모델을 열지 못했다 — {e}")
                return False
        self.ready.emit()
        return True

    def _take(self) -> QImage | None:
        with self._lock:
            frame, self._pending = self._pending, None
        return frame
