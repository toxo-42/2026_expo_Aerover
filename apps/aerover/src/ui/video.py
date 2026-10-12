"""실시간 영상 표시 — 받은 QImage 를 종횡비 유지로 그린다.

탐지 박스는 **축소된 픽스맵 위에** 그린다. 원본 좌표에 축소 배율만 곱하면
되고, 레터박스 오프셋을 따로 계산하지 않아도 된다 (픽스맵 자체가 영상이다).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from src.ui.detect_classes import style

NO_SIGNAL = "NO SIGNAL"
BOX_WIDTH = 4
LABEL_PT = 11
LABEL_PAD = 4           # 라벨 글자와 바탕 사각형 사이 여백


class VideoView(QFrame):
    """box_width · label_pt 는 화면 픽셀 기준이다 — 작은 PiP 에서는 얇고 작게 준다."""

    clicked = Signal()

    def __init__(self, parent=None, box_width: int = BOX_WIDTH, label_pt: int = LABEL_PT) -> None:
        super().__init__(parent)
        self.setObjectName("video")
        self.box_width, self.label_pt = box_width, label_pt
        self._image: QImage | None = None
        self._boxes: list = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        self.label = QLabel(NO_SIGNAL)
        self.label.setObjectName("videoText")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.label)

    def set_top_inset(self, px: int) -> None:
        """위쪽 px 만큼은 영상을 그리지 않는다 — 윗변에 HUD 바가 얹힐 때 프레임
        윗부분이 바에 가려지지 않게 한다. 바뀔 때만 다시 배치한다."""
        lay = self.layout()
        if lay.contentsMargins().top() != 1 + px:
            lay.setContentsMargins(1, 1 + px, 1, 1)

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

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

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
        """클래스마다 색이 다른 두꺼운 박스 + 같은 색 바탕에 흰 글자 라벨 (`ui/detect_classes`)."""
        painter = QPainter(pixmap)
        painter.setFont(QFont(self.font().family(), self.label_pt, QFont.Weight.Bold))
        metrics = painter.fontMetrics()
        for cls, conf, x1, y1, x2, y2 in self._boxes:
            s = style(cls)
            color = QColor(s.color)
            x, y = int(x1 * scale), int(y1 * scale)
            painter.setPen(QPen(color, self.box_width))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(x, y, int((x2 - x1) * scale), int((y2 - y1) * scale))

            text = f"{s.name} {conf:.2f}"
            tw, th = metrics.horizontalAdvance(text) + 2 * LABEL_PAD, metrics.height() + LABEL_PAD
            # 박스가 화면 맨 위에 붙으면 라벨이 잘린다. 그때는 박스 안쪽에 그린다.
            ty = y - th if y >= th else y
            painter.fillRect(x - self.box_width // 2, ty, tw, th, color)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(x - self.box_width // 2 + LABEL_PAD, ty + LABEL_PAD // 2 + metrics.ascent(), text)
        painter.end()
