"""요구조자 탐지 페이지 — 콘티 4절.

목표 파이프라인은

    ArUco → solvePnP → 카메라 6DoF → YOLO 픽셀좌표 → 역투영 → 메시 위 마커

인데 3차(ArUco→GCP) 좌표계 정합이 보류라 **3D 맵 위에 마커를 찍을 수 없다.**
그래서 이 페이지는 모드가 둘이다:

    [3D 맵]   메시 뷰포트. 마커 자리는 비어 있다 (3차가 끝나야 채워진다)
    [실시간]  파이 영상 그대로. **좌표계 정합에 의존하지 않는다** —
              프레임 → YOLO → 화면 박스로 끝나므로 지금 만들 수 있다

영상은 셸의 `LinkHub` 에서 받는다. 파이가 한 번에 한 클라이언트만 받아서
여기서 따로 연결하지 않는다 — **연결은 드론 상태 페이지 한 곳에서만** 한다.

연결점의 형태는 콘티가 확정한 그대로다:

    detect(frame) -> list[(cls, conf, x1, y1, x2, y2)]   # run_detect.py 형식 유지
    backproject(pixel, cam_pose, mesh) -> (x, y, z)      # 시그니처만
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget)

from src.config import DETECT_MODEL_PATH
from src.core.detect import DetectWorker, model_available
from src.core.link import LinkHub
from src.gl.viewport import MeshViewport
from src.ui.palette import STATUS_OK_TEXT, STATUS_RED, TEXT_DIM
from src.ui.video import VideoView

HUD_MARGIN = 16         # 뷰어 가장자리와 HUD 사이 여백
LIST_WIDTH = 260
MODE_3D, MODE_LIVE = 0, 1
NO_LINK = "드론 상태 페이지에서 연결하세요"
DEFAULT_CONF = 0.50
MAX_LOG = 100           # 목록이 무한히 자라지 않게 자른다
STOP_WAIT_MS = 15000    # 정지 대기 — 모델을 여는 중이면 그게 끝나야 중단을 본다


class DetectPage(QWidget):
    def __init__(self, hub: LinkHub, parent=None) -> None:
        super().__init__(parent)
        self.hub = hub

        # 뷰어가 화면을 다 쓰고, 제어부는 그 위에 뜬다 (시안 3절).
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        # 마커 레이어는 아직 없다. 3차가 끝나면 여기에 값만 흘려넣는다.
        self.markers: list[tuple[float, float, float]] = []
        self.viewport = MeshViewport()
        self.video = VideoView()

        self.views = QStackedWidget()
        self.views.addWidget(self.viewport)     # MODE_3D
        self.views.addWidget(self.video)        # MODE_LIVE
        root.addWidget(self.views)

        # HUD 는 뷰어의 형제 위젯이다. 레이아웃에 넣지 않고 좌표로 띄운다.
        self.hud = self._build_hud()
        self.list_card = self._build_log()
        self.warn = self._build_warn()
        for w in (self.hud, self.list_card, self.warn):
            w.raise_()

        self.worker: DetectWorker | None = None
        self._error = ""            # 추론이 터진 사유. 다시 시작할 때까지 남긴다
        self.hub.frame_ready.connect(self._on_frame)
        self.hub.connected.connect(self._refresh_detect_btn)
        self.hub.closed.connect(self._on_link_closed)
        self._set_mode(MODE_3D)

    # ---- HUD 배치 ----

    def resizeEvent(self, event) -> None:
        """레이아웃이 아니라 좌표로 얹는다 — 뷰어 위에 떠 있어야 하기 때문이다."""
        super().resizeEvent(event)
        view = self.views.geometry()
        m = HUD_MARGIN

        self.hud.setGeometry(view.x() + m, view.y() + m,
                             view.width() - 2 * m, self.hud.sizeHint().height())

        right = view.right() - m - LIST_WIDTH + 1
        warn_h = 0 if self.warn.isHidden() else self.warn.sizeHint().height()
        gap = 8 if warn_h else 0
        self.warn.setGeometry(right, view.bottom() - m - warn_h + 1, LIST_WIDTH, warn_h)

        top = self.hud.geometry().bottom() + m
        bottom = view.bottom() - m - warn_h - gap
        self.list_card.setGeometry(right, max(top, bottom - 240), LIST_WIDTH,
                                   min(240, bottom - top))

    def _build_modes(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(0)
        self._modes = QButtonGroup(self)
        self._modes.setExclusive(True)
        for i, text, name in ((MODE_3D, "3D 맵", "segLeft"), (MODE_LIVE, "실시간", "segRight")):
            btn = QPushButton(text)
            btn.setObjectName(name)
            btn.setCheckable(True)
            self._modes.addButton(btn, i)
            bar.addWidget(btn)
        self._modes.button(MODE_3D).setChecked(True)
        self._modes.idClicked.connect(self._set_mode)
        return bar

    def _build_hud(self) -> QFrame:
        """상단 한 줄 — 모드 · 모델 · 신뢰도 · 시작 버튼."""
        box = QFrame(self)
        box.setObjectName("hud")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(14)

        lay.addLayout(self._build_modes())
        lay.addWidget(self._sep())

        lay.addWidget(QLabel("모델"))
        self.model_note = QLabel("")
        lay.addWidget(self.model_note)
        lay.addWidget(self._sep())

        # 슬라이더는 정수만 다룬다. 0.05 단위로 쓰려고 100배 해서 담는다.
        self.conf = QSlider(Qt.Orientation.Horizontal)
        self.conf.setRange(5, 95)
        self.conf.setSingleStep(5)
        self.conf.setValue(int(DEFAULT_CONF * 100))
        self.conf.setFixedWidth(140)
        self.conf.valueChanged.connect(self._on_conf)
        self.conf_note = QLabel(f"{DEFAULT_CONF:.2f}")
        self.conf_note.setObjectName("dim")
        self.conf_note.setFixedWidth(32)
        lay.addWidget(QLabel("신뢰도"))
        lay.addWidget(self.conf)
        lay.addWidget(self.conf_note)

        lay.addStretch(1)

        self.detect_note = QLabel("")
        self.detect_note.setObjectName("dim")
        lay.addWidget(self.detect_note)

        self.detect_btn = QPushButton("탐지 시작")
        self.detect_btn.setEnabled(False)
        self.detect_btn.clicked.connect(self._toggle_detect)
        lay.addWidget(self.detect_btn)
        return box

    @staticmethod
    def _sep() -> QFrame:
        line = QFrame()
        line.setObjectName("hudSep")
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(1)
        return line

    def _build_log(self) -> QFrame:
        box = QFrame(self)
        box.setObjectName("hud")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("탐지 목록")
        title.setStyleSheet("font-weight:600;")
        self.log_note = QLabel("비어 있음")
        self.log_note.setObjectName("dim")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.log_note)
        lay.addLayout(head)

        self.log = QListWidget()
        self.log.setObjectName("log")
        self.log.setFrameShape(QFrame.Shape.NoFrame)
        lay.addWidget(self.log, 1)
        return box

    def _build_warn(self) -> QFrame:
        """좌표계 정합 경고 — 앰버 배너. 3차가 끝나면 통째로 사라진다."""
        box = QFrame(self)
        box.setObjectName("banner")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)

        head = QLabel("⚠ 좌표계 정합 없음")
        head.setObjectName("bannerHead")
        sub = QLabel("3D 마커를 띄울 수 없다 (3차 ArUco→GCP 필요)")
        sub.setObjectName("bannerSub")
        sub.setWordWrap(True)
        lay.addWidget(head)
        lay.addWidget(sub)
        return box

    def _set_mode(self, mode: int) -> None:
        self.views.setCurrentIndex(mode)
        if mode == MODE_LIVE:
            # 모드를 켠 시점에 링크가 없으면 왜 검은지 알려준다.
            if not self.hub.active:
                self.video.clear(NO_LINK)
        else:
            self._stop_detect()         # 3D 위에는 아직 찍을 곳이 없다
        # 실시간 탐지는 좌표계와 무관하다. 경고는 3D 모드에서만 뜬다.
        self.warn.setVisible(mode == MODE_3D)
        self._refresh_detect_btn()
        self.resizeEvent(None)

    def _refresh_detect_btn(self) -> None:
        """버튼은 **셋이 모두 맞을 때만** 열린다. 왜 잠겼는지도 같이 적는다."""
        if model_available():
            self.model_note.setText(DETECT_MODEL_PATH.name)
            self.model_note.setStyleSheet(f"color:{STATUS_OK_TEXT}; font-weight:600;")
        else:
            self.model_note.setText("✕ 없음")
            self.model_note.setStyleSheet(f"color:{STATUS_RED}; font-weight:600;")

        if self.worker is not None:
            self.detect_btn.setEnabled(True)
            return

        reason = ("YOLO 모델이 없다 — " + str(DETECT_MODEL_PATH.name) if not model_available() else
                  "실시간 모드에서만 탐지한다" if self.views.currentIndex() != MODE_LIVE else
                  NO_LINK if not self.hub.active else "")
        self.detect_btn.setEnabled(not reason)
        # 추론이 터졌으면 그 사유가 우선이다 — 워커가 끝나며 이 함수를 부르는데,
        # 여기서 덮어쓰면 실패 이유가 화면에 한 순간도 남지 않는다.
        self._set_detect_note(self._error or reason,
                              STATUS_RED if self._error else TEXT_DIM)

    def _set_detect_note(self, text: str, color: str) -> None:
        self.detect_note.setText(text)
        self.detect_note.setStyleSheet(f"color:{color};")

    # ---- 탐지 ----

    def _toggle_detect(self) -> None:
        if self.worker is None:
            self._start_detect()
        else:
            self._stop_detect()

    def _start_detect(self) -> None:
        self.worker = DetectWorker(self.conf.value() / 100)
        self.worker.ready.connect(self._on_detect_ready)
        self.worker.result.connect(self._on_result)
        self.worker.failed.connect(self._on_detect_failed)
        self.worker.finished.connect(self._on_detect_finished)
        self.worker.start()
        self._error = ""
        self.detect_btn.setText("탐지 정지")
        # ultralytics·torch import 와 가중치 읽기에 몇 초가 걸린다. 그동안 화면이
        # 아무 말도 없으면 멈춘 것처럼 보인다.
        self._set_detect_note("모델 여는 중…", TEXT_DIM)

    def _stop_detect(self) -> None:
        if self.worker is None:
            return
        self.worker.stop()
        # 모델을 여는 중이면 그게 끝나야 스레드가 중단을 본다. 2초로는 모자란다.
        self.worker.wait(STOP_WAIT_MS)

    def _on_detect_finished(self) -> None:
        self.worker = None
        self.detect_btn.setText("탐지 시작")
        self.video.set_boxes([])
        self._refresh_detect_btn()

    def _on_detect_ready(self) -> None:
        self.detect_note.setText("")

    def _on_detect_failed(self, message: str) -> None:
        self._error = message

    def _on_conf(self, value: int) -> None:
        self.conf_note.setText(f"{value / 100:.2f}")
        if self.worker is not None:
            self.worker.conf = value / 100      # 돌아가는 중에도 바로 반영된다

    def _on_result(self, boxes: list) -> None:
        self.video.set_boxes(boxes)
        if not boxes:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        for cls, conf, *_ in boxes:
            self.log.insertItem(0, f"{cls}  {conf:.2f}   {stamp}")
        while self.log.count() > MAX_LOG:
            self.log.takeItem(self.log.count() - 1)
        self.log_note.setText(f"{self.log.count()}건")

    # ---- 링크 ----

    def _on_frame(self, image: QImage, raw: bytes) -> None:
        # 3D 모드에서는 그릴 필요가 없다. 스케일링이 매 프레임 도는 것을 막는다.
        if self.views.currentIndex() != MODE_LIVE:
            return
        self.video.set_frame(image)
        if self.worker is not None:
            self.worker.submit(image)   # 워커가 바쁘면 이 프레임은 버려진다

    def _on_link_closed(self) -> None:
        self._stop_detect()
        self.video.clear(NO_LINK)
        self._refresh_detect_btn()

    def shutdown(self) -> None:
        """MainWindow.closeEvent 규약 — 추론 스레드를 남기지 않는다."""
        self._stop_detect()
