"""3D 매핑 — 단계 정의와 단계 한 줄 위젯."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from src.ui.palette import ACCENT_BLUE, TEXT_DIM


class State(Enum):
    LOCKED = "locked"      # 앞 단계가 안 끝났다
    READY = "ready"        # 실행할 수 있다
    RUNNING = "running"    # 진행 중
    DONE = "done"          # 끝났다


@dataclass
class StepSpec:
    key: str
    title: str
    action: str
    hint: str
    alt_action: str | None = None      # 있으면 카드에 작은 두 번째 버튼이 붙는다 (alt_triggered)


STEPS = [
    StepSpec("input", "입력", "폴더 선택", "촬영본을 고르고 ODM 에 넣을 수 있는지 검사한다"),
    StepSpec("subsample", "서브샘플", "선별 실행",
             "4장 묶음마다 가장 선명한 한 장을 고른다. 건너뛰기는 전체를 그대로 쓴다 — "
             "수집 간격을 이미 띄워 찍은 회차(40장 안팎)는 선별하면 ODM 이 못 돌 만큼 줄어든다",
             alt_action="건너뛰기"),
    StepSpec("odm", "ODM 제출", "실행", "NodeODM 에 올려 3D 로 재구성한다 (약 29분)"),
    StepSpec("crop", "배경 제거", "평면도 열기", "평면도에서 대상 영역을 직접 지정한다"),
    StepSpec("save", "결과 보기", "불러오기",
             "③④ 가 저장한 모델을 띄운다. 저장은 자동이라 따로 누를 것이 없다"),
]


STATUS_WIDTH = 96      # 제목 오른쪽 상태 글자 자리. 넘치면 … 로 줄이고 툴팁에 전문
CARD_WIDTH = 180       # 레일 가운데에 놓이는 카드 폭 (Figma v2 Compact Stepper)
RAIL_LENGTH = 12       # 점과 카드 사이 세로선
DOT_SIZE = 20
ACTION_WIDTH = 90      # 버튼이 하나일 때
PAIR_WIDTH = 78        # 버튼이 둘일 때 각각

# 상태 점 — 잠금/준비/진행/완료를 색이 아니라 형태로도 구분한다 (palette.py #stepDot*).
# 잠긴 것은 테두리만, 준비는 블루 테두리, 진행은 블루 채움 + 가로줄, 완료는 시안 채움 + 체크.
DOTS = {
    State.LOCKED: ("stepDotLocked", ""),
    State.READY: ("stepDotReady", ""),
    State.RUNNING: ("stepDotRunning", "−"),
    State.DONE: ("stepDotDone", "✓"),
}


class StepRow(QWidget):
    """단계 한 칸 — 세로 레일 위의 카드 + 그 아래 상태 점. 잠금 상태를 흐림이 아니라
    형태로 구분한다 (콘티 6절).

        │        ← 레일 선 (첫 단계 위에도 선만 둔다)
      ┌────┐
      │제목 상태│  ← 설명글은 화면에 두지 않고 툴팁으로 (시안 2절)
      │[버튼]│
      └────┘
        │
        ●        ← 이 단계의 상태 점. 다음 단계로 넘어가는 자리에 놓인다
                    (마지막 단계는 이어질 곳이 없어 점·선이 없다)

    점 아래 선은 다음 칸의 맨 위 선이 채운다. 카드들이 선으로 이어져야 하므로
    부르는 쪽은 StepRow 를 간격 0 으로 쌓는다.
    """

    triggered = Signal(str)
    alt_triggered = Signal(str)
    warn_clicked = Signal(str)      # 제목줄 ⚠ — 경고를 보고 넘길지 정한다

    def __init__(self, index: int, spec: StepSpec, last: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.index = index
        self._state = State.LOCKED

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        center = Qt.AlignmentFlag.AlignHCenter

        outer.addWidget(self._rail(), 0, center)
        # 점은 카드 아래에 붙는다. 마지막 단계도 상태는 계속 들고 있어야 해서
        # 위젯은 만들되 선과 함께 숨긴다.
        self.dot = QLabel()
        self.dot.setFixedSize(DOT_SIZE, DOT_SIZE)
        self.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        below = self._rail()
        for w in (below, self.dot):
            w.setVisible(not last)

        card = QFrame()
        card.setObjectName("stepCard")
        card.setFixedWidth(CARD_WIDTH)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        outer.addWidget(card, 0, center)
        outer.addWidget(below, 0, center)
        outer.addWidget(self.dot, 0, center)

        head = QHBoxLayout()
        head.setSpacing(4)
        self.title = QLabel(spec.title)
        self.title.setStyleSheet("font-weight:600;")
        self.title.setToolTip(spec.hint)      # 설명글은 여기로 들어간다
        # 상태는 제목 바로 옆에 짧게. 긴 글은 툴팁과 패널 아래 기록 카드가 받는다.
        self.detail = QLabel("")

        # 경고가 있을 때만 상태 글자 옆에 뜬다. 누르면 경고를 무시하고 넘어갈 수 있다 —
        # 재촬영이 불가능한 회차도 있다.
        self.warn = QPushButton("⚠")
        self.warn.setObjectName("stepWarn")
        self.warn.hide()
        self.warn.clicked.connect(lambda: self.warn_clicked.emit(spec.key))

        head.addStretch(1)
        head.addWidget(self.title)
        head.addWidget(self.detail)
        head.addWidget(self.warn)
        head.addStretch(1)
        lay.addLayout(head)

        # 두 번째 버튼이 있으면 주 버튼과 같은 폭으로 나란히 둔다
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addStretch(1)
        self.button = QPushButton(spec.action)
        self.button.setObjectName("ghost")
        self.button.setEnabled(False)
        self.button.clicked.connect(lambda: self.triggered.emit(spec.key))
        row.addWidget(self.button)
        self.alt_button: QPushButton | None = None
        if spec.alt_action:
            self.alt_button = QPushButton(spec.alt_action)
            self.alt_button.setObjectName("ghost")
            self.alt_button.setEnabled(False)
            self.alt_button.clicked.connect(lambda: self.alt_triggered.emit(spec.key))
            row.addWidget(self.alt_button)
        row.addStretch(1)
        lay.addLayout(row)
        for btn in (self.button, self.alt_button):
            if btn is not None:
                btn.setFixedWidth(PAIR_WIDTH if self.alt_button else ACTION_WIDTH)

        # 진행 중에는 버튼 아래에 얇은 막대가 들어선다 (시안 2절 — 동적 위젯 전환).
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.hide()
        lay.addWidget(self.bar)

        self.set_state(State.LOCKED)

    @staticmethod
    def _rail() -> QFrame:
        line = QFrame()
        line.setObjectName("rail")
        line.setFixedSize(2, RAIL_LENGTH)
        return line

    def set_state(self, state: State, detail: str | None = None) -> None:
        self._state = state
        name, glyph = DOTS[state]
        self.dot.setObjectName(name)
        # 끝난 단계는 체크. 번호 대신 레일 위 순서가 "몇 번째"를 말한다.
        self.dot.setText(glyph)
        self.dot.style().unpolish(self.dot)
        self.dot.style().polish(self.dot)
        # 진행 중인 단계만 상태 글자를 기능색으로 — 지금 도는 곳이 어디인지 눈에 띄게
        self.detail.setStyleSheet(f"color:{ACCENT_BLUE if state is State.RUNNING else TEXT_DIM};")

        # 완료된 단계도 다시 실행할 수 있다 — 입력을 바꾸거나 다시 돌리는 건 정상 흐름이다.
        self.button.setEnabled(state is not State.LOCKED)
        self.button.setObjectName("" if state is State.READY else "ghost")
        self.button.setText("취소" if state is State.RUNNING else self.spec.action)
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)

        if self.alt_button is not None:
            self.alt_button.setEnabled(state in (State.READY, State.DONE))
        self.bar.setVisible(state is State.RUNNING)
        if detail is not None:
            self.set_detail(detail)

    def set_detail(self, text: str, tip: str = "") -> None:
        """카드가 180px 뿐이라 긴 글은 카드를 다 잡아먹는다. 화면에는 한 줄로
        줄여 쓰고 전문은 툴팁으로 보낸다."""
        metrics = QFontMetrics(self.detail.font())
        self.detail.setText(metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, STATUS_WIDTH))
        self.detail.setToolTip(tip or text)

    def set_warning(self, tip: str) -> None:
        """빈 문자열이면 ⚠ 를 숨긴다."""
        self.warn.setVisible(bool(tip))
        self.warn.setToolTip(tip)

    @property
    def state(self) -> State:
        return self._state

    def set_progress(self, value: int, maximum: int = 100) -> None:
        self.bar.setMaximum(maximum)
        self.bar.setValue(value)
