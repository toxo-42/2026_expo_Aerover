"""상단 HUD 상태 아이콘 — 배터리 · 링크 · GPS.

에셋 대신 직접 그린다. 모양(칸 수·막대 수·점)과 색이 값에서 바로 나오므로
상태별 SVG 를 따로 둘 필요가 없다. 판정 기준과 색은 **호출하는 쪽이 정한다** —
아이콘은 받은 값을 그리기만 한다.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from src.ui.palette import TEXT_DIM, TEXT_PRIMARY

ICON_H = 16


class _HudIcon(QWidget):
    """공통 틀 — 크기 고정, 값 보관, 미수신이면 흐린 윤곽."""

    WIDTH = ICON_H

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._color = TEXT_DIM
        self._on = False            # False 면 미수신 — 윤곽만 흐리게
        self.setFixedSize(self.sizeHint())

    def sizeHint(self) -> QSize:
        return QSize(self.WIDTH, ICON_H)

    def _painter(self) -> QPainter:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        return p

    def _outline(self) -> QColor:
        return QColor(TEXT_PRIMARY if self._on else TEXT_DIM)


class BatteryIcon(_HudIcon):
    """몸통 + 단자 + 5칸. 한 칸이 20% — 경고(40%)·위험(20%) 경계가 칸 경계와 맞는다."""

    CELLS = 5
    BODY_W, BODY_H = 28, 14
    NUB_W, NUB_H = 3, 6       # + 단자
    PAD = 2                   # 몸통 안쪽 여백이자 칸 사이 간격
    WIDTH = BODY_W + NUB_W + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pct = 0

    def set_level(self, pct: int | None, color: str = TEXT_DIM) -> None:
        """pct 가 None 이면 미수신 — 빈 몸통만 흐리게 그린다."""
        self._on, self._pct, self._color = pct is not None, pct or 0, color
        self.update()

    def paintEvent(self, event) -> None:
        p = self._painter()
        outline = self._outline()

        body = QRectF(0.5, 1.5, self.BODY_W, self.BODY_H)
        p.setPen(QPen(outline, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body, 3, 3)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(outline)
        p.drawRoundedRect(QRectF(body.right(), body.center().y() - self.NUB_H / 2,
                                 self.NUB_W, self.NUB_H), 1, 1)

        if not self._on:
            return
        # 올림 — 1% 라도 한 칸은 남긴다. 0칸이 되는 건 정말 0% 일 때뿐이다.
        filled = min(self.CELLS, math.ceil(max(self._pct, 0) / (100 / self.CELLS)))
        inner = body.adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        cell_w = (inner.width() - self.PAD * (self.CELLS - 1)) / self.CELLS
        p.setBrush(QColor(self._color))
        for i in range(filled):
            p.drawRect(QRectF(inner.left() + i * (cell_w + self.PAD), inner.top(),
                              cell_w, inner.height()))


class LinkIcon(_HudIcon):
    """계단식 신호 막대 4개. LQ(0~100) 25 마다 한 칸. 빈 막대도 윤곽은 남겨
    "몇 칸 중 몇 칸"이 보이게 한다."""

    BARS = 4
    BAR_W, GAP = 3, 2
    WIDTH = BARS * BAR_W + (BARS - 1) * GAP + 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lq = 0

    def set_level(self, lq: int | None, color: str = TEXT_DIM) -> None:
        """lq 가 None 이면 미수신. 0 이면 막대 없이 윤곽만 (끊김)."""
        self._on, self._lq, self._color = lq is not None, lq or 0, color
        self.update()

    def paintEvent(self, event) -> None:
        p = self._painter()
        filled = min(self.BARS, math.ceil(max(self._lq, 0) / (100 / self.BARS))) if self._on else 0
        # 받고는 있는데 LQ 0 (끊김) 이면 빈 막대 윤곽을 상태색으로 — 미수신과 구분한다.
        empty = QColor(self._color) if self._on and not filled else self._outline()
        bottom = ICON_H - 1
        for i in range(self.BARS):
            h = ICON_H * (i + 1) / self.BARS - 1
            bar = QRectF(0.5 + i * (self.BAR_W + self.GAP), bottom - h, self.BAR_W, h)
            if i < filled:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(self._color))
            else:
                p.setPen(QPen(empty, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(bar, 1, 1)


class GpsIcon(_HudIcon):
    """조준경 — 원 + 십자 눈금 + 가운데 점. 링은 위성 수에 따른 색,
    가운데 점은 fix 를 믿을 수 있을 때만 채운다."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._fixed = False

    def set_state(self, on: bool, fixed: bool = False, color: str = TEXT_DIM) -> None:
        self._on, self._fixed, self._color = on, fixed, color
        self.update()

    def paintEvent(self, event) -> None:
        p = self._painter()
        c = QPointF(ICON_H / 2, ICON_H / 2)
        ring = QColor(self._color) if self._on else self._outline()

        p.setPen(QPen(ring, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, 5, 5)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):     # 상하좌우 눈금
            p.drawLine(c + QPointF(dx * 5, dy * 5), c + QPointF(dx * 7.5, dy * 7.5))

        if self._fixed:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ring)
            p.drawEllipse(c, 2, 2)
