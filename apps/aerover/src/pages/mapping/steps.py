"""3D 매핑 — 단계 정의와 단계 한 줄 위젯."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout


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


STEPS = [
    StepSpec("input", "입력", "폴더 선택", "촬영본을 고르고 ODM 에 넣을 수 있는지 검사한다"),
    StepSpec("subsample", "서브샘플", "선별 실행", "4장 묶음마다 가장 선명한 한 장을 고른다"),
    StepSpec("odm", "ODM 제출", "실행", "NodeODM 에 올려 3D 로 재구성한다 (약 29분)"),
    StepSpec("crop", "배경 제거", "평면도 열기", "평면도에서 대상 영역을 직접 지정한다"),
    StepSpec("save", "결과 보기", "불러오기",
             "③④ 가 저장한 모델을 띄운다. 저장은 자동이라 따로 누를 것이 없다"),
]


STATUS_WIDTH = 96      # 제목 오른쪽 상태 글자 자리. 넘치면 … 로 줄이고 툴팁에 전문


class StepRow(QFrame):
    """단계 카드. 잠금 상태를 흐림이 아니라 형태로 구분한다 (콘티 6절).

    구성은 **제목줄 + 카드 폭 전체 버튼**이다. 버튼을 오른쪽에 두면 250px 패널에서
    제목과 상태가 눌려 두 줄로 접힌다. 설명글은 화면에 두지 않고 툴팁으로 보낸다
    (시안 2절). 제목 오른쪽에 남는 글은 상태뿐이다 — "85장", "60%" 처럼 짧게.
    """

    triggered = Signal(str)

    def __init__(self, index: int, spec: StepSpec, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self.spec = spec
        self._state = State.LOCKED

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.index = index
        self.num = QLabel(str(index + 1))
        self.num.setObjectName("stepNum")
        self.num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.num.setFixedSize(20, 20)
        self.title = QLabel(spec.title)
        self.title.setStyleSheet("font-weight:600;")
        self.title.setToolTip(spec.hint)      # 설명글은 여기로 들어간다
        # 상태는 제목 오른쪽 끝에 짧게. 긴 글은 툴팁과 패널 아래 상태줄이 받는다.
        self.detail = QLabel("")
        self.detail.setObjectName("dim")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        head.addWidget(self.num)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.detail)
        outer.addLayout(head)

        self.button = QPushButton(spec.action)
        self.button.setObjectName("ghost")
        self.button.setEnabled(False)
        self.button.clicked.connect(lambda: self.triggered.emit(spec.key))
        outer.addWidget(self.button)

        # 진행 중에는 버튼 아래에 얇은 막대가 들어선다 (시안 2절 — 동적 위젯 전환).
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.hide()
        outer.addWidget(self.bar)

    def set_state(self, state: State, detail: str | None = None) -> None:
        self._state = state
        self.num.setObjectName({State.LOCKED: "stepNum", State.READY: "stepNumActive",
                                State.RUNNING: "stepNumActive", State.DONE: "stepNumDone"}[state])
        # 끝난 단계는 번호 대신 체크. 숫자는 "몇 번째"가 아니라 "무엇이 남았나"를 가린다.
        self.num.setText("✓" if state is State.DONE else str(self.index + 1))
        self.num.style().unpolish(self.num)
        self.num.style().polish(self.num)

        # 완료된 단계도 다시 실행할 수 있다 — 입력을 바꾸거나 다시 돌리는 건 정상 흐름이다.
        self.button.setEnabled(state is not State.LOCKED)
        self.button.setObjectName("" if state is State.READY else "ghost")
        self.button.setText("취소" if state is State.RUNNING else self.spec.action)
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)

        self.bar.setVisible(state is State.RUNNING)
        if detail is not None:
            self.set_detail(detail)

    def set_detail(self, text: str, tip: str = "") -> None:
        """패널이 250px 뿐이라 긴 글은 카드를 다 잡아먹는다. 화면에는 한 줄로
        줄여 쓰고 전문은 툴팁으로 보낸다."""
        metrics = QFontMetrics(self.detail.font())
        self.detail.setText(metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, STATUS_WIDTH))
        self.detail.setToolTip(tip or text)

    @property
    def state(self) -> State:
        return self._state

    def set_progress(self, value: int, maximum: int = 100) -> None:
        self.bar.setMaximum(maximum)
        self.bar.setValue(value)
