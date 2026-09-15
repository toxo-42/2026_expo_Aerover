"""실시간 영상 표시 — 받은 QImage 를 종횡비 유지로 그린다.

탐지 박스는 **축소된 픽스맵 위에** 그린다. 원본 좌표에 축소 배율만 곱하면
되고, 레터박스 오프셋을 따로 계산하지 않아도 된다 (픽스맵 자체가 영상이다).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from src.ui.palette import STATUS_RED

NO_SIGNAL = "NO SIGNAL"
BOX_WIDTH = 2


class VideoView(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("video")
        self._image: QImage | None = None
        self._boxes: list = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        self.label = QLabel(NO_SIGNAL)
        self.label.setObjectName("videoText")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.label)

    def set_frame(self, image: QImage) -> None:
        self._image = image
        self._redraw()

    def set_boxes(self, boxes: list) -> None:
        """(cls, conf, x1, y1, x2, y2) 목록. **원본 프레임 좌표.**"""
        self._boxes = boxes
        self._redraw()

    def clear(self, text: str = NO_SIGNAL) -> None:
        self._image = None
        self._boxes = []
        self.label.setPixmap(QPixmap())
        self.label.setText(text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._redraw()

    def _redraw(self) -> None:
        if self._image is None:
            return
        scaled = self._image.scaled(self.label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
        pixmap = QPixmap.fromImage(scaled)
        if self._boxes:
            self._draw_boxes(pixmap, scaled.width() / self._image.width())
        self.label.setPixmap(pixmap)

    def _draw_boxes(self, pixmap: QPixmap, scale: float) -> None:
        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor(STATUS_RED), BOX_WIDTH))
        painter.setFont(QFont(self.font().family(), 10, QFont.Weight.DemiBold))
        for cls, conf, x1, y1, x2, y2 in self._boxes:
            x, y = x1 * scale, y1 * scale
            painter.drawRect(int(x), int(y), int((x2 - x1) * scale), int((y2 - y1) * scale))
            # 박스가 화면 맨 위에 붙으면 라벨이 잘린다. 그때는 안쪽에 그린다.
            ty = y - 4 if y > 16 else y + 16
            painter.drawText(int(x), int(ty), f"{cls} {conf:.2f}")
        painter.end()
