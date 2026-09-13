"""3D 매핑 — 단계 정의와 단계 한 줄 위젯."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt, Signal
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


class StepRow(QFrame):
    """단계 한 줄. 잠금 상태를 흐림이 아니라 형태로 구분한다 (콘티 6절).

    설명글은 **화면에 두지 않고 툴팁으로 보낸다** (시안 2절). 카드에 남는 글은
    상태뿐이다 — "40장 검사 완료" 처럼 지금 무슨 일이 있었는지만.
    """

    triggered = Signal(str)

    def __init__(self, index: int, spec: StepSpec, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self.spec = spec
        self._state = State.LOCKED

        # detail 이 여러 줄로 늘어나면 카드도 같이 커져야 한다. 이걸 켜지 않으면
        # QFrame 이 한 줄 높이만 보고해 경고 목록이 카드 밖으로 잘린다.
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

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
        self.button = QPushButton(spec.action)
        self.button.setObjectName("ghost")
        self.button.setEnabled(False)
        self.button.clicked.connect(lambda: self.triggered.emit(spec.key))
        # 진행 중에는 버튼 자리 왼쪽에 얇은 막대가 들어선다 (시안 2절 — 동적 위젯 전환).
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(70)
        self.bar.hide()

        head.addWidget(self.num)
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.bar)
        head.addWidget(self.button)
        outer.addLayout(head)

        # 상태가 생기기 전에는 줄 자체가 없다. 설명글은 제목 툴팁에 있다.
        self.detail = QLabel("")
        self.detail.setObjectName("dim")
        self.detail.setWordWrap(True)
        self.detail.hide()
        outer.addWidget(self.detail)

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
        """패널이 250px 뿐이라 절대경로를 그대로 넣으면 카드를 다 잡아먹는다.
        화면에는 짧게 쓰고 전체 경로는 툴팁으로 보낸다."""
        self.detail.setText(text)
        self.detail.setToolTip(tip)
        self.detail.setVisible(bool(text))
        self.updateGeometry()      # 줄 수가 바뀌면 카드 높이도 다시 잡혀야 한다

    @property
    def state(self) -> State:
        return self._state

    def set_progress(self, value: int, maximum: int = 100) -> None:
        self.bar.setMaximum(maximum)
        self.bar.setValue(value)
