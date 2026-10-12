"""요구조자 탐지 페이지 — 콘티 4절.

목표 파이프라인은

    ArUco → solvePnP → 카메라 6DoF → YOLO 픽셀좌표 → 역투영 → 메시 위 마커

이고, 좌표는 전부 시연 좌표계 W (apps/aruco, `core/wframe.py`) 다.

    [3D 맵]   고른 회차의 메시를 `to_W.json` 으로 W 에 옮겨 띄우고, 탐지 자리에 클래스 색 핀 +
              드론 카메라(위치 · 촬영 범위). 오른쪽 위 작은 영상(PiP)으로 지금 무엇이 보이는지 같이 본다
    [실시간]  파이 영상 + YOLO 박스 (PiP 를 누르면 여기로 온다)

**3D 맵은 고른다 — 최신 회차가 아니다.** 시연장에서는 ODM 을 돌려 보여주기만 하고
(오래 걸린다) 탐지는 미리 만든 좋은 맵 하나로 한다. 고른 것은 앱을 다시 켜도 남는다.

영상은 셸의 `LinkHub` 에서 받는다. 파이가 한 번에 한 클라이언트만 받아서
여기서 따로 연결하지 않는다 — **연결은 드론 상태 페이지 한 곳에서만** 한다.

연결점의 형태는 콘티가 확정한 그대로다:

    detect(frame) -> list[(cls, conf, x1, y1, x2, y2)]   # run_detect.py 형식 유지
    backproject(pixel, cam_pose, mesh) -> (x, y, z)      # 시그니처만
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.config import DETECT_MODEL_PATH, DRONE_MESH_FILE, MODELS_DIR
from src.core import wframe
from src.core.detect import DetectWorker, model_available
from src.core.link import LinkHub
from src.gl import loader
from src.gl.viewport import MeshViewport
from src.ui.detect_classes import STYLES, style
from src.ui.palette import STATUS_OK, STATUS_OK_TEXT, STATUS_RED, TEXT_DIM, rgbf
from src.ui.video import VideoView

HUD_MARGIN = 16  # 뷰어 가장자리와 HUD 사이 여백
SIDE_W = 260  # 오른쪽 열(PiP · 요약) 폭
MODE_3D, MODE_LIVE = 0, 1
NO_LINK = "드론 상태 페이지에서 연결하세요"
DEFAULT_CONF = 0.50
STOP_WAIT_MS = 15000  # 정지 대기 — 모델을 여는 중이면 그게 끝나야 중단을 본다
MAP_KEY = "detect/map"  # QSettings — 고른 3D 맵 회차 이름
MIN_HITS = 10  # 이만큼 본 것만 요약 · 핀에 — 잠깐 스친 오검출 · 바닥에 맞은 광선 묶음을 거른다 (core/wframe MERGE_R 주석)
PIP_W, PIP_H = SIDE_W, SIDE_W * 3 // 4  # 4:3 — 스트림(1280×960)과 같은 비
HOVER_OFFSET = 14  # 호버 카드를 핀에서 이만큼 띄운다
GAP = 8  # 오른쪽 열 카드 사이
CAM_LIVE = tuple(round(c * 255) for c in rgbf(STATUS_OK))  # 맵 위 카메라 — 지금 자세
CAM_LOST = tuple(
    round(c * 255) for c in rgbf(TEXT_DIM)
)  # 마지막 자세 — 탐지 중 마커를 놓쳤을 때. 탐지를 멈추면 지운다


class DetectPage(QWidget):
    def __init__(self, hub: LinkHub, parent=None) -> None:
        super().__init__(parent)
        self.hub = hub

        # 뷰어가 화면을 다 쓰고, 제어부는 그 위에 뜬다 (시안 3절).
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        self.map_mesh: wframe.Mesh | None = (
            None  # 역투영 대상 (W). 맵을 고르면 채워진다
        )
        self._estimator: wframe.PoseEstimator | None = None  # 처음 탐지할 때 만든다
        self.victims = wframe.Victims()
        self.viewport = MeshViewport()
        self.video = VideoView()
        if (
            DRONE_MESH_FILE.is_file()
        ):  # 없으면 뷰포트가 사각뿔로 그린다 (tools/drone_mesh.py 로 만든다)
            drone = np.load(DRONE_MESH_FILE)
            self.viewport.set_drone_model(drone["vertices"], drone["faces"])
        self._camera: wframe.CameraView | None = None  # 맵에 그린 마지막 카메라

        self.views = QStackedWidget()
        self.views.addWidget(self.viewport)  # MODE_3D
        self.views.addWidget(self.video)  # MODE_LIVE
        root.addWidget(self.views)

        # HUD 는 뷰어의 형제 위젯이다. 레이아웃에 넣지 않고 좌표로 띄운다.
        self.hud = self._build_hud()
        self.summary_card = self._build_summary()
        self.hover_card = self._build_hover()
        self.warn = self._build_warn()
        self.pip = VideoView(self, box_width=2, label_pt=8)
        self.pip.setObjectName("pip")       # 큰 영상과 다른 테두리 (palette QFrame#pip)
        self.pip.setToolTip("드론 영상 — 누르면 크게")
        self.pip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pip.clicked.connect(lambda: self._modes.button(MODE_LIVE).click())
        for w in (self.hud, self.pip, self.summary_card, self.warn, self.hover_card):
            w.raise_()
        self._shown: list[
            tuple[wframe.Victim, str]
        ] = []  # 핀 순서 그대로 (객체, "요구조자 #2")
        self._hovered: wframe.Victim | None = None
        self.viewport.marker_hovered.connect(self._on_marker_hovered)

        self.worker: DetectWorker | None = None
        self._error = ""  # 추론이 터진 사유. 다시 시작할 때까지 남긴다
        self.hub.frame_ready.connect(self._on_frame)
        self.hub.connected.connect(self._refresh_detect_btn)
        self.hub.closed.connect(self._on_link_closed)
        self._fill_maps()
        self._set_mode(MODE_3D)

    # ---- HUD 배치 ----

    def resizeEvent(self, event) -> None:
        """레이아웃이 아니라 좌표로 얹는다 — 뷰어 위에 떠 있어야 하기 때문이다."""
        super().resizeEvent(event)
        view = self.views.geometry()
        m = HUD_MARGIN

        # 상단 바는 여백 없이 뷰어 윗변에 붙인다. PiP·요약·경고는 떠 있는 카드다.
        self.hud.setGeometry(
            view.x(), view.y(), view.width(), self.hud.sizeHint().height()
        )
        # 실시간 영상은 바 아래에만 그린다 — 바가 불투명이라 프레임 윗부분이 가려진다.
        # 3D 뷰포트는 바 뒤로 이어져도 되므로 그대로 둔다.
        self.video.set_top_inset(self.hud.height())

        right = view.right() - m - SIDE_W + 1
        warn_h = 0 if self.warn.isHidden() else self.warn.sizeHint().height()
        self.warn.setGeometry(right, view.bottom() - m - warn_h + 1, SIDE_W, warn_h)

        # 오른쪽 열: PiP(3D 모드에서만) 아래에 요약
        top = self.hud.geometry().bottom() + m
        self.pip.setGeometry(right, top, PIP_W, PIP_H)
        if (
            not self.pip.isHidden()
        ):  # isVisible 은 페이지가 뜨기 전엔 늘 False 다 — 숨김 여부만 본다
            top += PIP_H + GAP
        h = self.summary_card.heightForWidth(
            SIDE_W
        )  # 줄바꿈 라벨이라 폭에 따라 높이가 다르다
        self.summary_card.setGeometry(
            right, top, SIDE_W, h if h > 0 else self.summary_card.sizeHint().height()
        )

    def _build_modes(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(0)
        self._modes = QButtonGroup(self)
        self._modes.setExclusive(True)
        for i, text, name in (
            (MODE_3D, "3D 맵", "segLeft"),
            (MODE_LIVE, "실시간", "segRight"),
        ):
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
        box.setObjectName("hudBar")  # 떠 있는 카드가 아니라 뷰어 윗변에 붙은 바
        lay = QHBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(14)

        lay.addLayout(self._build_modes())
        lay.addWidget(self._sep())

        lay.addWidget(QLabel("맵"))
        self.map_box = QComboBox()
        self.map_box.setToolTip(
            "탐지 위치를 찍을 3D 맵 — to_W.json 이 있는 회차만 나온다"
        )
        lay.addWidget(self.map_box)
        lay.addWidget(self._sep())

        lay.addWidget(QLabel("모델"))
        self.model_note = QLabel("")
        lay.addWidget(self.model_note)
        lay.addWidget(self._sep())

        # 신뢰도는 처음에 맞춰두는 값이라 바에는 값만 칩으로 두고, 누르면 슬라이더가 뜬다.
        # 값은 늘 보여야 한다 — 박스가 몇 개 뜨는지가 이 값에 달려 있다.
        self.conf_btn = QPushButton(f"신뢰도 {DEFAULT_CONF:.2f}")
        self.conf_btn.setObjectName("hudButton")
        self.conf_btn.setToolTip("탐지 신뢰도 기준 — 눌러서 조절")
        self.conf_btn.clicked.connect(self._show_conf_popup)
        self.conf_popup = self._build_conf_popup()
        lay.addWidget(self.conf_btn)

        lay.addStretch(1)

        self.detect_note = QLabel("")
        self.detect_note.setObjectName("dim")
        lay.addWidget(self.detect_note)

        self.detect_btn = QPushButton("탐지 시작")
        self.detect_btn.setEnabled(False)
        self.detect_btn.clicked.connect(self._toggle_detect)
        lay.addWidget(self.detect_btn)
        return box

    def _build_conf_popup(self) -> QFrame:
        """신뢰도 슬라이더 팝업. Qt.Popup 이라 바깥을 누르면 저절로 닫힌다."""
        box = QFrame(self, Qt.WindowType.Popup)
        box.setObjectName("hud")
        # 독립 창이라 배경을 투명하게 둬야 카드(#hud) 의 둥근 모서리 밖이 칠해지지 않는다
        box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        # 슬라이더는 정수만 다룬다. 0.05 단위로 쓰려고 100배 해서 담는다.
        self.conf = QSlider(Qt.Orientation.Horizontal)
        self.conf.setRange(5, 95)
        self.conf.setSingleStep(5)
        self.conf.setValue(int(DEFAULT_CONF * 100))
        self.conf.setFixedWidth(180)
        self.conf.valueChanged.connect(self._on_conf)
        # hint = QLabel("낮추면 더 많이, 높이면 확실한 것만")
        # hint.setObjectName("dim")
        lay.addWidget(self.conf)
        # lay.addWidget(hint)
        return box

    def _show_conf_popup(self) -> None:
        """칩 바로 아래, 왼쪽 정렬로 띄운다."""
        self.conf_popup.move(
            self.conf_btn.mapToGlobal(QPoint(0, self.conf_btn.height() + 6))
        )
        self.conf_popup.show()

    @staticmethod
    def _sep() -> QFrame:
        line = QFrame()
        line.setObjectName("hudSep")
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(1)
        return line

    def _build_summary(self) -> QFrame:
        """클래스별 개수 한 줄 — 청중에게 늘 보이는 것은 이것뿐이다. 객체 하나하나는 핀 호버로 본다."""
        box = QFrame(self)
        box.setObjectName("hud")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)

        head = QHBoxLayout()
        title = QLabel("탐지")
        title.setStyleSheet("font-weight:600;")
        self.summary_note = QLabel("")
        self.summary_note.setObjectName("dim")
        self.summary_note.setToolTip(
            f"{MIN_HITS}번 이상 본 것만 요약 · 핀에 올린다. 나머지는 후보"
        )
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.summary_note)
        lay.addLayout(head)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.summary)
        self._set_summary({}, "")
        return box

    def _set_summary(self, counts: dict[str, int], note: str) -> None:
        """세 클래스를 늘 같은 순서 · 색 점으로 — 0 이어도 보인다 (늘어나는 것을 보게)."""
        parts = [
            f"<span style='color:{s.color}'>●</span> {s.name} <b>{counts.get(c, 0)}</b>"
            for c, s in STYLES.items()
        ]
        self.summary.setText("&nbsp;&nbsp; ".join(parts))
        self.summary_note.setText(note)

    def _build_hover(self) -> QFrame:
        """핀에 마우스를 올리면 커서 옆에 뜨는 카드. 마우스를 가로채지 않는다."""
        box = QFrame(self)
        box.setObjectName("hud")
        box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 8)
        self.hover_text = QLabel()
        self.hover_text.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.hover_text)
        box.hide()
        return box

    def _build_warn(self) -> QFrame:
        """3D 맵을 못 띄울 때의 앰버 배너. 맵이 뜨면 사라진다."""
        box = QFrame(self)
        box.setObjectName("banner")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)

        head = QLabel("⚠ 3D 맵 없음")
        head.setObjectName("bannerHead")
        self.warn_sub = QLabel("to_W.json 이 있는 회차가 없다 (aruco/align_model.py)")
        self.warn_sub.setObjectName("bannerSub")
        self.warn_sub.setWordWrap(True)
        lay.addWidget(head)
        lay.addWidget(self.warn_sub)
        return box

    # ---- 3D 맵 ----

    def _fill_maps(self) -> None:
        """to_W.json 이 있는 회차로 콤보를 채우고, 지난번에 고른 것을 다시 고른다."""
        saved = QSettings("aerover", "aerover").value(MAP_KEY, "")
        self.map_box.blockSignals(True)
        self.map_box.clear()
        for d in wframe.list_maps(MODELS_DIR):
            self.map_box.addItem(d.name, str(d))
        i = self.map_box.findText(saved)
        self.map_box.setCurrentIndex(max(i, 0) if self.map_box.count() else -1)
        self.map_box.blockSignals(False)
        self.map_box.currentIndexChanged.connect(self._on_map)
        self._on_map()

    def _on_map(self) -> None:
        d = self.map_box.currentData()
        self.map_mesh = None
        if d is not None:
            d = Path(d)
            path = wframe.map_file(d)
            try:
                M = wframe.load_to_w(d)
                self.viewport.set_model(wframe.model_to_w(loader.load(path), M))
                self.viewport.set_grid(*wframe.sector_grid())
                self.map_mesh = wframe.load_mesh_w(path, M)
                QSettings("aerover", "aerover").setValue(MAP_KEY, d.name)
                self.map_box.setToolTip(str(path))
            except Exception as e:
                self.warn_sub.setText(f"{path.name} 을 읽지 못했다 — {e}")
        self._reset_victims()  # 다른 맵의 좌표는 섞지 않는다
        if self.worker is not None:
            self.worker.locator = self._locator()
        self._set_mode(self.views.currentIndex())

    def _locator(self):
        """고른 맵을 묶은 `wframe.locate`. 맵이 없으면 None — 워커는 박스만 낸다."""
        mesh = self.map_mesh
        if mesh is None:
            return None
        if self._estimator is None:
            self._estimator = wframe.load_estimator()
        est = self._estimator
        return lambda gray, uv: wframe.locate(gray, uv, est, mesh)

    def _reset_victims(self) -> None:
        self.victims = wframe.Victims()
        self._shown = []
        self._on_marker_hovered(-1)
        self._set_summary({}, "")
        self.viewport.set_markers([], [])
        self._camera = None
        self.viewport.set_camera(None)

    def _set_mode(self, mode: int) -> None:
        self.views.setCurrentIndex(mode)
        # 탐지는 두 모드 모두에서 돈다 — 3D 모드에서는 맵 위 핀 + PiP 로 본다.
        if not self.hub.active:  # 모드를 켠 시점에 링크가 없으면 왜 검은지 알려준다
            self.current_video().clear(NO_LINK)
        self.pip.setVisible(mode == MODE_3D)
        if mode != MODE_3D:
            self._on_marker_hovered(-1)  # 맵이 가려지면 커서가 핀을 떠난 것과 같다
        self.warn.setVisible(mode == MODE_3D and self.map_mesh is None)
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

        reason = (
            "YOLO 모델이 없다 — " + str(DETECT_MODEL_PATH.name)
            if not model_available()
            else NO_LINK
            if not self.hub.active
            else ""
        )
        self.detect_btn.setEnabled(not reason)
        # 추론이 터졌으면 그 사유가 우선이다 — 워커가 끝나며 이 함수를 부르는데,
        # 여기서 덮어쓰면 실패 이유가 화면에 한 순간도 남지 않는다.
        self._set_detect_note(
            self._error or reason, STATUS_RED if self._error else TEXT_DIM
        )

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
        self.worker.located.connect(self._on_located)
        self.worker.locator = self._locator()
        self._reset_victims()  # 탐지 한 번 = 한 판의 지도
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
        self.current_video().set_boxes([])
        # 멈추면 드론 · 시야선을 지운다 — 남겨 두면 아직 스캔 중인 것처럼 보인다. 핀 · 요약(결과)은 남긴다.
        self._camera = None
        self.viewport.set_camera(None)
        self._refresh_detect_btn()

    def _on_detect_ready(self) -> None:
        self.detect_note.setText("")

    def _on_detect_failed(self, message: str) -> None:
        self._error = message

    def _on_conf(self, value: int) -> None:
        self.conf_btn.setText(f"신뢰도 {value / 100:.2f}")
        if self.worker is not None:
            self.worker.conf = value / 100  # 돌아가는 중에도 바로 반영된다

    def current_video(self) -> VideoView:
        """지금 보이는 영상 — 3D 모드면 PiP, 실시간 모드면 큰 화면. 프레임 · 박스는 이쪽에만 그린다."""
        return self.video if self.views.currentIndex() == MODE_LIVE else self.pip

    def _show_camera(self, live: bool) -> None:
        if self._camera is not None:
            self.viewport.set_camera(
                self._camera.C,
                self._camera.footprint,
                CAM_LIVE if live else CAM_LOST,
                body=wframe.drone_body(self._camera.R),
            )

    def _on_result(self, boxes: list) -> None:
        self.current_video().set_boxes(boxes)
        # 맵이 있으면 요약은 객체 단위(`_on_located`)다. 없으면 지금 화면의 박스 수를 센다.
        if self.worker is None or self.worker.locator is not None:
            return
        counts: dict[str, int] = {}
        for cls, *_ in boxes:
            counts[cls] = counts.get(cls, 0) + 1
        self._set_summary(counts, "지금 화면")

    def _on_located(self, loc: wframe.Located, found: list | None) -> None:
        """자세 상태를 상단에 적고, 탐지를 객체 단위로 모아 목록 · 핀을 다시 그린다.
        세 클래스 모두 올린다 — 핀 색으로 구분한다 (`ui/detect_classes`)."""
        if loc.camera is None:
            self._set_detect_note(f"자세 없음 — 마커 {loc.markers}개", STATUS_RED)
            self._show_camera(live=False)
            return
        self._set_detect_note(f"마커 {loc.markers}개", TEXT_DIM)
        self._camera = loc.camera
        self._show_camera(live=True)
        stamp = datetime.now().strftime("%H:%M:%S")
        for box, xyz in found:
            self.victims.add(box[0], xyz, stamp)
        if not found:
            return
        # 클래스 안 번호(요구조자 #2)는 먼저 본 순서 — 객체가 늘어도 앞 번호는 그대로다
        shown = [v for v in self.victims.items if v.hits >= MIN_HITS]
        counts: dict[str, int] = {}
        self._shown = []
        for v in shown:
            counts[v.cls] = counts.get(v.cls, 0) + 1
            self._shown.append((v, f"{style(v.cls).name} #{counts[v.cls]}"))
        # 걸러진 수도 보인다 — 0인데 후보가 쌓이고 있으면 아직 덜 본 것이다
        self._set_summary(counts, f"후보 {len(self.victims.items) - len(shown)}")
        self.viewport.set_markers(
            np.array([v.xyz for v in shown]).reshape(-1, 3),
            [style(v.cls).rgb for v in shown],
        )
        if self._hovered is not None:
            self._fill_hover()  # 보고 있는 동안에도 관측 수 · 위치가 갱신된다

    # ---- 핀 호버 ----

    def _on_marker_hovered(self, i: int) -> None:
        """핀 번호 → 그 객체를 기억한다. 다음 프레임에 핀 순서가 바뀌어도 같은 객체를 보인다."""
        self._hovered = self._shown[i][0] if 0 <= i < len(self._shown) else None
        if self._hovered is None:
            self.hover_card.hide()
            return
        self._fill_hover()
        self._place_hover(i)
        self.hover_card.show()

    def _fill_hover(self) -> None:
        found = next(((v, name) for v, name in self._shown if v is self._hovered), None)
        if found is None:  # 맵을 바꿨거나 목록이 비었다
            self._on_marker_hovered(-1)
            return
        v, name = found
        where = wframe.sector(v.xyz)
        self.hover_text.setText(
            f"<b><span style='color:{style(v.cls).color}'>●</span> {name}</b><br>"
            f"{where + ' 구역' if where else '판 밖'} · {wframe.height_text(v.xyz[2])}<br>"
            f"<span style='color:{TEXT_DIM}'>{v.hits}번 관측 · 마지막 {v.last}</span>"
        )
        self.hover_card.adjustSize()

    def _place_hover(self, i: int) -> None:
        """핀 오른쪽 아래에 붙인다. 페이지를 넘으면 핀 반대쪽으로 뒤집는다.
        커서가 아니라 핀 자리를 쓴다 — 카드가 어느 핀 것인지 분명하고, 커서 전역 좌표에 기대지 않는다."""
        pin = self.viewport.marker_pos(i)
        if pin is None:
            return
        c = self.viewport.mapTo(self, pin.toPoint())
        w, h = self.hover_card.width(), self.hover_card.height()
        x = (
            c.x() + HOVER_OFFSET
            if c.x() + HOVER_OFFSET + w <= self.width()
            else c.x() - HOVER_OFFSET - w
        )
        y = (
            c.y() + HOVER_OFFSET
            if c.y() + HOVER_OFFSET + h <= self.height()
            else c.y() - HOVER_OFFSET - h
        )
        self.hover_card.move(max(0, x), max(0, y))

    # ---- 링크 ----

    def _on_frame(self, image: QImage, raw: bytes) -> None:
        # 보이는 쪽에만 그린다 — 안 보이는 쪽까지 매 프레임 스케일링하지 않는다.
        self.current_video().set_frame(image)
        if self.worker is not None:
            self.worker.submit(image)  # 워커가 바쁘면 이 프레임은 버려진다

    def _on_link_closed(self) -> None:
        self._stop_detect()
        self.video.clear(NO_LINK)
        self.pip.clear(NO_LINK)
        self._refresh_detect_btn()

    def shutdown(self) -> None:
        """MainWindow.closeEvent 규약 — 추론 스레드를 남기지 않고, GL 자원은 위젯이 살아 있을 때 놓는다."""
        self._stop_detect()
        self.viewport.release_gl()
