"""평면도 드래그로 경계 상자를 고르는 위젯 — 콘티 3절 ④단계.

자동화가 아니라 사람이 지정하는 이유는 4절이 아니라 제약이다 — GPS 없이 재구성하면
ODM 좌표계는 회차마다 원점·축·스케일이 임의다. 실제로 1차-B 는 x -43~41,
`20260906_full` 은 x -4.9~3.1 이었다. 경계값을 코드에 박을 수 없다.

### 높이는 z 가 아니라 "바닥 위 높이"다

`20260906_full` 은 바닥이 44.4° 기울어 있었다. 그대로 z 로 칠하면 평면도가 온통
밝아 대상이 안 보이고, z 로 자르면 바닥이 **대각선으로** 썰린다.
바닥 평면을 빼고 남은 잔차로 칠하면 바닥이 평평해지고 대상만 솟는다.
근거와 상세는 `core.cropper` 의 모듈 주석.

### 색

높이(z)를 **단일 색상 명도 램프**로 칠한다 — 순차 스케일의 규칙이다 (무지개 금지).
프로젝트 팔레트(콘티 6절)의 앰버를 쓰고, 어두운 뷰포트 배경 위에 얹는다.
명도는 단조 증가(0.40 → 0.88)하고, 가장 어두운 단계도 배경과 1.9:1 이라
"데이터 없음"과 구분된다.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from src.core import cropper
from src.ui.palette import ACCENT_BLUE, TEXT_DIM, VIEWPORT_BG

GRID = 720             # 긴 축 기준 격자 칸 수
MIN_DRAG_PX = 6        # 이보다 작으면 클릭으로 본다 (선택 해제)

# 높이 램프 — 단일 색상(블루), 명도만 오른다. 어두운 배경 위 순차 스케일.
# 앰버는 이제 경고 전용이라 데이터 인코딩에 쓰지 않는다. 명도는 단조 증가하고
# 가장 어두운 단계가 뷰포트 배경과 2.2:1 이다 (콘티 18절 기준 1.9:1 이상).
HEIGHT_RAMP = ("#22518F", "#2E6BB8", "#4A90E2", "#7FB6F0", "#BEDCFA")


def _ramp_lut() -> np.ndarray:
    """램프를 256단계 (256,3) uint8 LUT 로 편다."""
    stops = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in HEIGHT_RAMP],
                     dtype=float)
    x = np.linspace(0, 1, len(stops))
    t = np.linspace(0, 1, 256)
    return np.stack([np.interp(t, x, stops[:, i]) for i in range(3)], 1).astype(np.uint8)


LUT = _ramp_lut()


class PlanView(QWidget):
    """위에서 내려다본 평면도. 좌드래그로 남길 영역을 지정한다."""

    box_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(420, 360)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._xy = np.zeros((0, 2))
        self._h = np.zeros(0)
        self._img: QImage | None = None
        self._buf: np.ndarray | None = None      # QImage 가 참조하는 메모리를 붙잡아 둔다
        self._extent = (0.0, 0.0, 1.0, 1.0)      # 그려진 데이터 범위 x0,y0,x1,y1
        self._rect_px = QRect()                  # 화면 위 이미지 위치
        self._drag_from: QPoint | None = None
        self._drag_to: QPoint | None = None
        self.box: tuple[float, float, float, float] | None = None   # x0,y0,x1,y1

    # ---- 데이터 ----

    def set_points(self, xy: np.ndarray, h: np.ndarray) -> None:
        """평면 좌표와 **바닥 위 높이**. 높이 필터는 `rebuild` 가 따로 받는다."""
        self._xy = np.asarray(xy, dtype=float)
        self._h = np.asarray(h, dtype=float)
        self.rebuild()

    def rebuild(self, h_lo: float | None = None, h_hi: float | None = None) -> None:
        """높이 범위 안의 점만 평면도로 굽는다.

        바닥을 높이로 떨궈내면 **평면도에서 바닥이 사라진다** — 남길 영역이 눈에 띈다.
        """
        xy, h = self._xy, self._h
        if len(xy) == 0:
            self._img = None
            self.update()
            return
        if h_lo is not None:
            keep = (h >= h_lo) & (h <= h_hi)
            xy, h = xy[keep], h[keep]
        if len(xy) == 0:
            self._img = None
            self.update()
            return

        # 격자는 데이터 기준으로 정사각형이어야 한다 — 안 그러면 평면도가 일그러진다
        x0, y0 = self._xy[:, 0].min(), self._xy[:, 1].min()
        x1, y1 = self._xy[:, 0].max(), self._xy[:, 1].max()
        cell = max(x1 - x0, y1 - y0) / GRID or 1.0
        nx = max(int(np.ceil((x1 - x0) / cell)), 1)
        ny = max(int(np.ceil((y1 - y0) / cell)), 1)

        ix = np.clip(((xy[:, 0] - x0) / cell).astype(int), 0, nx - 1)
        iy = np.clip(((xy[:, 1] - y0) / cell).astype(int), 0, ny - 1)

        top = np.full(nx * ny, -np.inf)
        np.maximum.at(top, iy * nx + ix, h)             # 칸마다 가장 높은 점
        filled = np.isfinite(top)

        lo, hi = np.percentile(h, [1, 99])
        norm = np.clip((top[filled] - lo) / max(hi - lo, 1e-9), 0, 1)

        buf = np.empty((ny, nx, 3), dtype=np.uint8)
        buf[:] = np.array(QColor(VIEWPORT_BG).getRgb()[:3], dtype=np.uint8)
        buf.reshape(-1, 3)[filled] = LUT[(norm * 255).astype(int)]

        self._buf = np.ascontiguousarray(buf[::-1])     # 데이터 y 는 위로, 이미지 행은 아래로
        self._img = QImage(self._buf.data, nx, ny, 3 * nx, QImage.Format.Format_RGB888)
        self._extent = (x0, y0, x1, y1)
        self.update()

    def clear_box(self) -> None:
        self.box = self._drag_from = self._drag_to = None
        self.update()
        self.box_changed.emit()

    # ---- 좌표 변환 ----

    def _fit_rect(self) -> QRect:
        """이미지를 위젯 안에 비율 유지로 앉힌 자리."""
        if self._img is None:
            return QRect()
        w, h = self._img.width(), self._img.height()
        s = min(self.width() / w, self.height() / h)
        rw, rh = int(w * s), int(h * s)
        return QRect((self.width() - rw) // 2, (self.height() - rh) // 2, rw, rh)

    def _to_data(self, pt: QPoint) -> tuple[float, float]:
        x0, y0, x1, y1 = self._extent
        r = self._rect_px
        u = (pt.x() - r.x()) / max(r.width(), 1)
        v = (pt.y() - r.y()) / max(r.height(), 1)
        return x0 + u * (x1 - x0), y1 - v * (y1 - y0)

    def _to_px(self, x: float, y: float) -> QPoint:
        x0, y0, x1, y1 = self._extent
        r = self._rect_px
        return QPoint(int(r.x() + (x - x0) / max(x1 - x0, 1e-9) * r.width()),
                      int(r.y() + (y1 - y) / max(y1 - y0, 1e-9) * r.height()))

    # ---- 입력 ----

    def mousePressEvent(self, e) -> None:
        if e.button() is Qt.MouseButton.LeftButton and self._img is not None:
            self._drag_from = self._drag_to = e.pos()
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_from is not None:
            self._drag_to = e.pos()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_from is None:
            return
        a, b = self._drag_from, e.pos()
        self._drag_from = self._drag_to = None
        if (b - a).manhattanLength() < MIN_DRAG_PX:
            self.box = None                     # 짧게 누르면 선택 해제
        else:
            ax, ay = self._to_data(a)
            bx, by = self._to_data(b)
            self.box = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
        self.update()
        self.box_changed.emit()

    # ---- 그리기 ----

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(VIEWPORT_BG))
        if self._img is None:
            p.setPen(QColor(TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "표시할 점이 없다")
            return

        self._rect_px = self._fit_rect()
        p.drawImage(self._rect_px, self._img)

        if self._drag_from is not None and self._drag_to is not None:
            self._draw_rect(p, QRect(self._drag_from, self._drag_to).normalized(), True)
        elif self.box is not None:
            x0, y0, x1, y1 = self.box
            self._draw_rect(p, QRect(self._to_px(x0, y1), self._to_px(x1, y0)).normalized(),
                            False)

    def _draw_rect(self, p: QPainter, r: QRect, dragging: bool) -> None:
        # 바깥을 덮어 "남는 영역"이 무엇인지 한눈에 보이게 한다
        if not dragging:
            shade = QColor(0, 0, 0, 110)
            full = self._rect_px
            p.fillRect(QRect(full.left(), full.top(), full.width(), r.top() - full.top()), shade)
            p.fillRect(QRect(full.left(), r.bottom(), full.width(), full.bottom() - r.bottom()),
                       shade)
            p.fillRect(QRect(full.left(), r.top(), r.left() - full.left(), r.height()), shade)
            p.fillRect(QRect(r.right(), r.top(), full.right() - r.right(), r.height()), shade)

        pen = QPen(QColor(ACCENT_BLUE), 2)
        pen.setStyle(Qt.PenStyle.DashLine if dragging else Qt.PenStyle.SolidLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(r))


class HeightLegend(QWidget):
    """높이 램프가 무엇을 뜻하는지 알려준다. 색만으로 두지 않는다."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(34)
        self._lo = self._hi = 0.0

    def set_range(self, lo: float, hi: float) -> None:
        self._lo, self._hi = lo, hi
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        bar = QRect(0, 0, self.width(), 12)
        for i in range(bar.width()):
            c = LUT[int(i / max(bar.width() - 1, 1) * 255)]
            p.fillRect(i, 0, 1, bar.height(), QColor(int(c[0]), int(c[1]), int(c[2])))
        p.setPen(QColor(TEXT_DIM))
        p.drawText(QRect(0, 14, self.width(), 20), Qt.AlignmentFlag.AlignLeft,
                   f"낮음 {self._lo:.2f}")
        p.drawText(QRect(0, 14, self.width(), 20), Qt.AlignmentFlag.AlignRight,
                   f"{self._hi:.2f} 높음")


class CropDialog(QDialog):
    """평면도에서 남길 영역을 고른다.

    z 를 먼저 좁히면 바닥이 평면도에서 사라져 대상이 드러난다 — 그다음 x·y 를 끈다.
    높이 스핀박스 기본값은 0.5~99.5 분위수다.
    """

    def __init__(self, xyz: np.ndarray, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("배경 제거 — 남길 영역 지정")
        self.resize(720, 720)
        self.xyz = xyz
        # 바닥이 기울어 있어도 "바닥 위 높이"는 뜻이 통한다 (core.cropper 주석)
        self.plane = cropper.fit_plane(xyz)
        self.height = cropper.height_above(xyz, self.plane)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        tip = QLabel("드래그로 남길 영역을 지정한다. 짧게 누르면 선택이 풀린다.\n"
                     f"바닥이 {cropper.tilt_degrees(self.plane):.1f}° 기울어 있다 — "
                     "높이는 z 가 아니라 바닥 기준이다.")
        tip.setObjectName("dim")
        lay.addWidget(tip)

        self.plan = PlanView()
        self.plan.box_changed.connect(self._update_info)
        lay.addWidget(self.plan, 1)

        self.legend = HeightLegend()
        lay.addWidget(self.legend)

        z_lo, z_hi = np.percentile(self.height, [0.5, 99.5])
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel("바닥 위 높이")
        label.setObjectName("dim")
        row.addWidget(label)
        h0, h1 = float(self.height.min()), float(self.height.max())
        self.z_min = self._spin(h0, h1, float(z_lo))
        self.z_max = self._spin(h0, h1, float(z_hi))
        row.addWidget(self.z_min)
        row.addWidget(QLabel("~"))
        row.addWidget(self.z_max)
        row.addStretch(1)
        reset = QPushButton("전체")
        reset.setObjectName("ghost")
        reset.clicked.connect(self._reset)
        row.addWidget(reset)
        lay.addLayout(row)

        self.info = QLabel("")
        self.info.setObjectName("dim")
        lay.addWidget(self.info)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("취소")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("잘라내기")
        self.ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        lay.addLayout(buttons)

        self.plan.set_points(xyz[:, :2], self.height)
        self._rebuild()

    def _spin(self, lo: float, hi: float, value: float) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(2)
        s.setSingleStep(max((hi - lo) / 100, 0.01))
        s.setValue(value)
        s.valueChanged.connect(self._rebuild)
        return s

    def _reset(self) -> None:
        self.z_min.setValue(self.z_min.minimum())
        self.z_max.setValue(self.z_max.maximum())
        self.plan.clear_box()

    def _rebuild(self) -> None:
        lo, hi = self.z_min.value(), self.z_max.value()
        if lo > hi:
            lo, hi = hi, lo
        self.plan.rebuild(lo, hi)
        self.legend.set_range(lo, hi)
        self._update_info()

    def _update_info(self) -> None:
        lo, hi = sorted((self.z_min.value(), self.z_max.value()))
        keep = (self.height >= lo) & (self.height <= hi)
        if self.plan.box is not None:
            x0, y0, x1, y1 = self.plan.box
            keep &= ((self.xyz[:, 0] >= x0) & (self.xyz[:, 0] <= x1)
                     & (self.xyz[:, 1] >= y0) & (self.xyz[:, 1] <= y1))
        n, total = int(keep.sum()), len(self.xyz)
        picked = self.plan.box is not None
        self.ok.setEnabled(picked)
        self.info.setText(
            f"남는 정점 {n:,} / {total:,} ({n / total * 100:.1f}%)"
            + ("" if picked else "  —  아직 영역을 지정하지 않았다"))

    def bounds(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(lo, hi, plane) — core.cropper.crop 에 그대로 넘긴다.

        세 번째 성분은 z 가 아니라 **바닥 위 높이**다. plane 을 같이 넘겨야 뜻이 맞는다.
        """
        x0, y0, x1, y1 = self.plan.box
        z0, z1 = sorted((self.z_min.value(), self.z_max.value()))
        return np.array([x0, y0, z0]), np.array([x1, y1, z1]), self.plane
