"""라벨 도구 — 상자를 그리고 지운다. 이 저장소의 PySide6 를 그대로 쓴다.

    uv run python -m tools.label_gui label/20260916_185141_r1

### 왜 만들었나

모형이 **20~30픽셀**이다. 일반 라벨 도구로 1280x960 을 화면에 맞춰 띄우면 모형이
점으로 보여 상자를 칠 수가 없다. 그래서 **확대**를 이 작업에 맞춰 넣었다 —
휠을 굴리면 마우스 밑 지점을 중심으로 커진다.

바깥 도구(X-AnyLabeling 은 1GB, labelImg 는 PyQt5 라 이 환경의 PySide6 와 부딪친다)를
들이는 대신 이미 있는 PySide6 로 만든 이유이기도 하다.

### 조작

    좌클릭 드래그   상자 그리기 (현재 클래스로)
    상자 안 클릭    선택 (겹쳐 있으면 가장 작은 상자가 잡힌다)
    Delete          선택한 상자 지우기
    1 / 2           클래스 바꾸기 (person / vehicle). 선택 중이면 그 상자의 클래스도 바뀐다
    휠              확대 · 축소       가운데 버튼 드래그  이동
    F               화면에 맞추기
    A / D           이전 · 다음 장    (넘어갈 때 자동 저장)
    Ctrl+Z          되돌리기
    Ctrl+S          저장

### 저장

**장을 넘길 때와 창을 닫을 때 자동으로 저장한다.** 라벨 작업은 몇 시간짜리라 저장을
잊으면 그만큼이 날아간다. 형식은 YOLO 텍스트 (`tools/labelio.py`).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt                             # noqa: E402
from PySide6.QtGui import (QColor, QFont, QImage, QKeySequence, QPainter,  # noqa: E402
                           QPen, QShortcut)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel,          # noqa: E402
                               QMainWindow, QWidget)

from tools import use_utf8_stdout                                          # noqa: E402
from tools.autolabel import CLASSES                                        # noqa: E402
from tools.labelio import Box, load, pick, save, zoom_at                   # noqa: E402
from src.core.imgcheck import list_images                                  # noqa: E402

COLORS = ["#EF4444", "#0090FF"]         # CLASSES 순서와 같다
SELECTED = "#22C55E"
MIN_DRAG = 3.0                          # 이보다 작게 끌면 상자가 아니라 클릭으로 본다
ZOOM_STEP = 1.25
ZOOM_MIN, ZOOM_MAX = 0.2, 20.0
UNDO_DEPTH = 50
VIEWPORT_BG = "#131922"


class Canvas(QWidget):
    """이미지 한 장과 그 위의 상자들. 좌표는 전부 **이미지 픽셀**로 들고 있는다."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.image: QImage | None = None
        self.boxes: list[Box] = []
        self.cls = 0
        self.selected: int | None = None
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        self._drag_from: QPointF | None = None
        self._drawing: Box | None = None
        self._panning = False
        self._pan_from = QPointF(0, 0)
        self._undo: list[list[Box]] = []
        self._fitted = False
        self.on_change = lambda: None

    # ---- 좌표 변환 ----

    def to_image(self, pos) -> QPointF:
        return QPointF((pos.x() - self.offset.x()) / self.zoom,
                       (pos.y() - self.offset.y()) / self.zoom)

    def to_widget(self, x: float, y: float) -> QPointF:
        return QPointF(x * self.zoom + self.offset.x(), y * self.zoom + self.offset.y())

    def fit(self) -> None:
        if self.image is None or not self.image.width():
            return
        self.zoom = min(self.width() / self.image.width(),
                        self.height() / self.image.height())
        self.offset = QPointF((self.width() - self.image.width() * self.zoom) / 2,
                              (self.height() - self.image.height() * self.zoom) / 2)
        self._fitted = True
        self.update()

    # ---- 상태 ----

    def set_image(self, image: QImage, boxes: list[Box]) -> None:
        self.image, self.boxes, self.selected = image, boxes, None
        self._undo.clear()
        self.fit()

    def push_undo(self) -> None:
        self._undo.append([Box(b.cls, b.x1, b.y1, b.x2, b.y2) for b in self.boxes])
        del self._undo[:-UNDO_DEPTH]

    def undo(self) -> None:
        if not self._undo:
            return
        self.boxes = self._undo.pop()
        self.selected = None
        self.on_change()
        self.update()

    def delete_selected(self) -> None:
        if self.selected is None:
            return
        self.push_undo()
        del self.boxes[self.selected]
        self.selected = None
        self.on_change()
        self.update()

    def set_class(self, cls: int) -> None:
        self.cls = cls
        if self.selected is not None:       # 선택 중이면 그 상자를 고치겠다는 뜻이다
            self.push_undo()
            self.boxes[self.selected].cls = cls
            self.on_change()
        self.update()

    # ---- 그리기 ----

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(VIEWPORT_BG))
        if self.image is None:
            return

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 1.0)
        painter.drawImage(QRectF(self.offset.x(), self.offset.y(),
                                 self.image.width() * self.zoom,
                                 self.image.height() * self.zoom), self.image)

        for i, box in enumerate(self.boxes):
            b = box.normalized()
            chosen = i == self.selected
            painter.setPen(QPen(QColor(SELECTED if chosen else COLORS[box.cls % len(COLORS)]),
                                3 if chosen else 2))
            painter.drawRect(QRectF(self.to_widget(b.x1, b.y1), self.to_widget(b.x2, b.y2)))

        if self._drawing is not None:
            b = self._drawing.normalized()
            painter.setPen(QPen(QColor(COLORS[self.cls % len(COLORS)]), 2,
                                Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(self.to_widget(b.x1, b.y1), self.to_widget(b.x2, b.y2)))

    # ---- 마우스 ----

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning, self._pan_from = True, event.position()
            return
        if event.button() != Qt.MouseButton.LeftButton or self.image is None:
            return
        self._drag_from = self.to_image(event.position())
        self._drawing = None

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            self.offset += event.position() - self._pan_from
            self._pan_from = event.position()
            self.update()
            return
        if self._drag_from is None:
            return
        now = self.to_image(event.position())
        if (abs(now.x() - self._drag_from.x()) > MIN_DRAG
                or abs(now.y() - self._drag_from.y()) > MIN_DRAG):
            self._drawing = Box(self.cls, self._drag_from.x(), self._drag_from.y(),
                                now.x(), now.y())
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            return
        if event.button() != Qt.MouseButton.LeftButton or self._drag_from is None:
            return

        if self._drawing is not None:
            self.push_undo()
            self.boxes.append(self._drawing.normalized())
            self.selected = len(self.boxes) - 1
            self.on_change()
        else:
            # 끌지 않았다 = 고르겠다는 뜻 (겹치면 가장 작은 것 — `labelio.pick`)
            point = self.to_image(event.position())
            self.selected = pick(self.boxes, point.x(), point.y())

        self._drag_from = self._drawing = None
        self.update()

    def wheelEvent(self, event) -> None:
        if self.image is None:
            return
        step = ZOOM_STEP if event.angleDelta().y() > 0 else 1 / ZOOM_STEP
        new_zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * step))
        cursor = event.position()
        x, y = zoom_at((self.offset.x(), self.offset.y()), self.zoom, new_zoom,
                       (cursor.x(), cursor.y()))
        self.zoom, self.offset = new_zoom, QPointF(x, y)
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.image is not None and not self._fitted:
            self.fit()


class LabelWindow(QMainWindow):
    def __init__(self, folder: Path) -> None:
        super().__init__()
        self.folder = folder
        self.images = list_images(folder)
        if not self.images:
            raise SystemExit(f"이미지가 없다: {folder}")
        self.index = 0

        self.canvas = Canvas()
        self.canvas.on_change = self._refresh

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        self.setCentralWidget(central)

        self.status = QLabel()
        self.status.setFont(QFont("", 10))
        self.statusBar().addWidget(self.status)

        bindings = [("D", lambda: self._go(1)), ("Right", lambda: self._go(1)),
                    ("A", lambda: self._go(-1)), ("Left", lambda: self._go(-1)),
                    ("Delete", self.canvas.delete_selected),
                    ("Backspace", self.canvas.delete_selected),
                    ("F", self.canvas.fit),
                    ("Ctrl+Z", self.canvas.undo),
                    ("Ctrl+S", self._save)]
        bindings += [(str(i + 1), (lambda c: lambda: self.canvas.set_class(c))(i))
                     for i in range(len(CLASSES))]
        for keys, slot in bindings:
            QShortcut(QKeySequence(keys), self, slot)

        self.resize(1400, 950)
        self.setWindowTitle(f"라벨 — {folder.name}")
        self._load()

    # ---- 장 이동 ----

    def _label_path(self) -> Path:
        return self.images[self.index].with_suffix(".txt")

    def _load(self) -> None:
        image = QImage(str(self.images[self.index]))
        self.canvas.set_image(image, load(self._label_path(), image.width(), image.height()))
        self._refresh()

    def _save(self) -> None:
        image = self.canvas.image
        if image is None:
            return
        save(self._label_path(), self.canvas.boxes, image.width(), image.height())
        self._refresh(saved=True)

    def _go(self, step: int) -> None:
        self._save()                        # 넘어가기 전에 무조건 저장한다
        self.index = max(0, min(len(self.images) - 1, self.index + step))
        self._load()

    def _refresh(self, saved: bool = False) -> None:
        counts = [0] * len(CLASSES)
        for b in self.canvas.boxes:
            if 0 <= b.cls < len(counts):
                counts[b.cls] += 1
        detail = " · ".join(f"{name} {counts[i]}" for i, name in enumerate(CLASSES))
        self.status.setText(
            f"  [{self.index + 1}/{len(self.images)}] {self.images[self.index].name}   "
            f"{detail}   |  그릴 클래스: {CLASSES[self.canvas.cls]} (1/2)   "
            f"|  A·D 이동   휠 확대   F 맞춤   Del 삭제   Ctrl+Z 되돌리기"
            + ("   |  저장함" if saved else ""))

    def closeEvent(self, event) -> None:
        self._save()                        # 창을 닫아도 잃지 않는다
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> None:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description="YOLO 라벨을 그리고 지운다")
    ap.add_argument("folder", type=Path, help="이미지와 .txt 가 있는 폴더")
    args = ap.parse_args(argv)
    if not args.folder.is_dir():
        raise SystemExit(f"폴더가 없다: {args.folder}")

    app = QApplication(sys.argv)
    window = LabelWindow(args.folder)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
