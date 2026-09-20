"""앱 셸 — 좌측 사이드바 + 페이지 3개.

탭바가 아니라 사이드바를 쓴다. 페이지가 3개뿐이고 각 페이지가 화면을 넓게
쓰기 때문이다 (콘티 1절).
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QMainWindow,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from src.config import ICON_DIR
from src.core import telemetry
from src.core.link import LinkHub
from src.pages.detect import DetectPage
from src.pages.mapping import MappingPage
from src.pages.status import StatusPage
from src.ui.palette import TEXT_DIM, TEXT_PRIMARY

SIDEBAR_WIDTH = 64
NAV_ICON_SIZE = 26
ICON_FILL = 'fill="#1f1f1f"'    # 받은 SVG 의 기본색. 이 자리를 바꿔 끼워 색을 입힌다

# (라벨, 아이콘, 만드는 법) — 순서가 곧 화면 순서다.
# 라벨은 화면에 쓰지 않고 툴팁으로만 보인다 — 사이드바를 아이콘 레일로 줄여 영상에 자리를 내준다.
# 링크가 필요한 페이지는 셸이 가진 허브를 받는다. 직접 연결하지는 못한다.
PAGES = [
    ("드론 상태", "monitor_heart.svg", lambda hub: StatusPage(hub)),
    ("3D 매핑", "map_search.svg", lambda hub: MappingPage()),
    ("요구조자 탐지", "person_search.svg", lambda hub: DetectPage(hub)),
]


def _tinted(svg: str, color: str) -> QPixmap:
    """SVG 의 fill 을 바꿔 그린다. 레티나에서 뭉개지지 않게 3배로 그려 두고
    QIcon 이 줄여 쓰게 한다."""
    renderer = QSvgRenderer(QByteArray(svg.replace(ICON_FILL, f'fill="{color}"').encode()))
    pix = QPixmap(NAV_ICON_SIZE * 3, NAV_ICON_SIZE * 3)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return pix


def _nav_icon(path) -> QIcon:
    """선택 안 된 아이콘은 흐리게, 선택된 것은 진하게. 체크 상태(On/Off)마다
    그림을 따로 넣어 두면 버튼이 체크될 때 Qt 가 알아서 바꿔 그린다."""
    svg = path.read_text()
    icon = QIcon()
    icon.addPixmap(_tinted(svg, TEXT_DIM), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_tinted(svg, TEXT_PRIMARY), QIcon.Mode.Normal, QIcon.State.On)
    return icon


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

        for *_, make in PAGES:
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

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for i, (label, icon, _) in enumerate(PAGES):
            btn = QPushButton()
            btn.setIcon(_nav_icon(ICON_DIR / icon))
            btn.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
            btn.setToolTip(label)
            btn.setObjectName("nav")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: self.stack.setCurrentIndex(idx))
            self._nav_group.addButton(btn, i)
            lay.addWidget(btn)

        lay.addStretch(1)
        return bar
