"""계기판 한 줄 — 라벨 / 값 / 보조값."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.ui.palette import TEXT_PRIMARY

NO_VALUE = "—"


class MetricRow(QWidget):
    def __init__(self, key: str, parent=None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 5, 0, 5)
        lay.setSpacing(1)

        self.key = QLabel(key)
        self.key.setObjectName("metricKey")
        lay.addWidget(self.key)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.value = QLabel(NO_VALUE)
        self.value.setObjectName("metricValue")
        self.sub = QLabel("")
        self.sub.setObjectName("metricSub")
        row.addWidget(self.value)
        row.addStretch(1)
        row.addWidget(self.sub)
        lay.addLayout(row)

    def set(self, value: str, sub: str = "", color: str | None = None) -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        # 상태색은 동적이라 QSS 가 아니라 여기서 직접 준다.
        self.value.setStyleSheet(f"color:{color or TEXT_PRIMARY};")

    def clear(self, sub: str = "") -> None:
        self.set(NO_VALUE, sub)
