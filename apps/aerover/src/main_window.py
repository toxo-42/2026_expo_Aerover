"""앱 셸 — 좌측 사이드바 + 페이지 3개.

탭바가 아니라 사이드바를 쓴다. 페이지가 3개뿐이고 각 페이지가 화면을 넓게
쓰기 때문이다 (콘티 1절).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from src.core import telemetry
from src.core.link import LinkHub
from src.pages.detect import DetectPage
from src.pages.mapping import MappingPage
from src.pages.status import StatusPage

SIDEBAR_WIDTH = 190

# (라벨, 만드는 법) — 순서가 곧 화면 순서다.
# 링크가 필요한 페이지는 셸이 가진 허브를 받는다. 직접 연결하지는 못한다.
PAGES = [
    ("드론 상태", lambda hub: StatusPage(hub)),
    ("3D 매핑", lambda hub: MappingPage()),
    ("요구조자 탐지", lambda hub: DetectPage(hub)),
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AeroVer 지상국")
        self.resize(1360, 860)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 파이는 한 번에 한 클라이언트만 받는다. 소유자는 여기 하나뿐이다.
        self.hub = LinkHub(self)
        # 텔레메트리 수신도 셸이 띄운다. 계기판은 스냅샷을 읽기만 한다.
        telemetry.start()

        self.stack = QStackedWidget()
        root.addWidget(self._build_sidebar())
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        for _, make in PAGES:
            self.stack.addWidget(make(self.hub))
        self._nav_group.buttons()[0].setChecked(True)
        self.stack.setCurrentIndex(0)

    def closeEvent(self, event) -> None:
        # 수신 스레드는 페이지가 아니라 허브 소유다.
        self.hub.disconnect_from()
        # 페이지가 스레드를 들고 있으면 여기서 정리한다.
        for i in range(self.stack.count()):
            page = self.stack.widget(i)
            if hasattr(page, "shutdown"):
                page.shutdown()
        super().closeEvent(event)

    def _build_sidebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(SIDEBAR_WIDTH)

        lay = QVBoxLayout(bar)
        lay.setContentsMargins(0, 16, 0, 12)
        lay.setSpacing(2)

        for text, obj in (("AeroVer", "brand"), ("재난 구조 드론 지상국", "brandSub")):
            label = QLabel(text)
            label.setObjectName(obj)
            label.setContentsMargins(12, 0, 12, 0)
            lay.addWidget(label)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for i, (label, _) in enumerate(PAGES):
            btn = QPushButton(label)
            btn.setObjectName("nav")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: self.stack.setCurrentIndex(idx))
            self._nav_group.addButton(btn, i)
            lay.addWidget(btn)

        lay.addStretch(1)
        return bar
