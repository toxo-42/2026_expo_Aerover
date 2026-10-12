"""텍스처 메시 뷰포트 — QOpenGLWidget.

ODM 산출물은 서브메시마다 텍스처가 달라 드로우콜을 파트 수만큼 낸다
(크롭본 45회). 이 규모에서는 문제되지 않는다.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import shiboken6

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QSurfaceFormat, QVector3D
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram,
                              QOpenGLTexture, QOpenGLVertexArrayObject)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from src.gl import loader
from src.gl.camera import OrbitCamera
from src.ui.palette import VIEWPORT_BG, rgbf

VERT_SRC = """
#version 330 core
layout(location = 0) in vec3 in_pos;
layout(location = 1) in vec3 in_nrm;
layout(location = 2) in vec2 in_uv;

uniform mat4 u_mvp;
uniform mat3 u_normal;

out vec3 v_nrm;
out vec2 v_uv;

void main() {
    v_nrm = normalize(u_normal * in_nrm);
    v_uv = in_uv;
    gl_Position = u_mvp * vec4(in_pos, 1.0);
}
"""

FRAG_SRC = """
#version 330 core
in vec3 v_nrm;
in vec2 v_uv;

uniform sampler2D u_tex;
uniform vec3 u_light_dir;   // 뷰 공간 기준 광원 방향

out vec4 frag;

void main() {
    vec3 albedo = texture(u_tex, v_uv).rgb;
    // 헤드라이트 + 양면 조명. 사진측량 메시는 법선이 뒤집힌 면이 섞여 있어
    // abs() 로 받지 않으면 군데군데 새까맣게 뜬다.
    float lambert = abs(dot(normalize(v_nrm), u_light_dir));
    float shade = 0.35 + 0.65 * lambert;
    frag = vec4(albedo * shade, 1.0);
}
"""


class _Part:
    """GL 자원 한 벌. VAO/VBO/IBO/텍스처."""

    def __init__(self) -> None:
        self.vao = QOpenGLVertexArrayObject()
        self.vbo: QOpenGLBuffer | None = None
        self.ibo: QOpenGLBuffer | None = None
        self.tex: QOpenGLTexture | None = None
        self.count = 0


HOVER_PX = 12           # 핀 중심에서 이 안이면 그 핀에 올린 것
GRID_W = 0.003          # 바닥 격자선 굵기 [m]
GRID_RGB = (226, 232, 240)      # 밝은 회백 — 풀 · 잔해 위에서 보이게
LABEL_BG = QColor(19, 25, 34, 210)      # 라벨 칩 — 뷰포트 바탕(VIEWPORT_BG) 반투명. 밝은 판 테두리 위에서도 읽힌다


class MeshViewport(QOpenGLWidget):
    """마우스 조작: 좌드래그 회전 · 휠 줌 · 중클릭(또는 Shift+좌) 패닝.

    버튼 없이 움직이면 가까운 핀을 골라 `marker_hovered(번호)` 를 낸다 (-1 = 없음).
    번호는 마지막 `set_markers` 에 넘긴 점의 순서다.
    """

    marker_hovered = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.camera = OrbitCamera()
        self._model: loader.Model | None = None
        self._pending: loader.Model | None = None   # GL 컨텍스트 준비 전 대기분
        self._parts: list[_Part] = []
        self._markers_dirty = False         # 핀이 바뀌었다 — 비었어도 한 번은 다시 올려 지운다
        self._marker_part: _Part | None = None
        self._camera: loader.SubMesh | None = None      # 드론 카메라 — 대기분 (마커와 같은 방식)
        self._camera_part: _Part | None = None
        self._drone: loader.SubMesh | None = None       # 드론 기체 메시 — 대기분. 깊이 테스트를 켜고 그린다
        self._drone_part: _Part | None = None
        self._drone_model: tuple[np.ndarray, np.ndarray] | None = None   # 기체 좌표 (삼각형 (F,3,3), 면 법선 (F,3))
        self._grid: loader.SubMesh | None = None        # 바닥 격자 — 대기분
        self._grid_part: _Part | None = None
        self._grid_labels: list[tuple[np.ndarray, str]] = []
        self._label_layer = _LabelLayer(self)
        self._marker_points = np.zeros((0, 3))
        self._marker_colors: list[tuple[int, int, int]] = []
        self._hover = -1
        self._program: QOpenGLShaderProgram | None = None
        self._last_pos = None
        self.setMouseTracking(True)

    # ---- 공개 API ----

    def load_model(self, path: str | Path) -> loader.Model:
        model = loader.load(path)
        self.set_model(model)
        return model

    def set_model(self, model: loader.Model) -> None:
        self.camera.fit(model.lo, model.hi, self._aspect())
        if self._program is None:
            self._pending = model          # initializeGL 에서 올린다
            self._model = model
            return
        self.makeCurrent()
        self._release_parts()
        self._model = model
        self._upload(model)
        self.doneCurrent()
        self.update()

    def set_markers(self, points: np.ndarray, colors: list[tuple[int, int, int]]) -> None:
        """모델 좌표의 점마다 핀 하나 (끝이 점에 닿는 마름모). 빈 배열이면 지운다.

        **메시에 가려도 보이게** 맨 위에 그린다 — 건물 안 요구조자가 사라지면 안 된다.
        """
        self._marker_points = np.asarray(points, float).reshape(-1, 3)
        self._marker_colors = list(colors)
        self._markers_dirty = True          # 메시는 그릴 때 만든다 — 크기가 시점에 따라 바뀐다
        self.update()

    def _pin_heights(self) -> np.ndarray:
        """핀마다 실제 높이 — 화면에서 늘 PIN_PX 픽셀이 되게. 그리기와 호버 판정이 같이 쓴다."""
        return PIN_PX * self.camera.world_per_px(self._marker_points, self.height())

    def set_grid(self, segments: np.ndarray, labels: list[tuple[np.ndarray, str]]) -> None:
        """바닥 격자 — 선분 (N,2,3) 과 (자리, 글자) 라벨. 빈 것이면 지운다. 메시에 가리면 가려진다."""
        tris = [t for a, b in np.asarray(segments, float).reshape(-1, 2, 3) for t in _ribbon(a, b, GRID_W)]
        self._grid = _flat_mesh(tris, GRID_RGB)
        self._grid_labels = list(labels)
        self.update()

    def set_drone_model(self, vertices: np.ndarray, faces: np.ndarray) -> None:
        """맵에 그릴 기체 메시 (기체 좌표: +Y 앞, +Z 위, m). 한 번만 준다. 없으면 사각뿔로 그린다."""
        tri = np.asarray(vertices, float)[np.asarray(faces)]
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        self._drone_model = (tri, n)

    def set_camera(self, apex: np.ndarray | None, corners: np.ndarray | None = None,
                   rgb: tuple[int, int, int] = (255, 255, 255), body: np.ndarray | None = None) -> None:
        """드론 카메라 — apex 에서 corners(4점, 테두리 순서)까지 시야선과 촬영 범위, apex 에 기체.

        기체 모델(`set_drone_model`)과 자세 body(3,3, 열 = 기체 X · Y · Z 의 모델 좌표 방향)가 있으면
        그 메시를, 없으면 작은 사각뿔을 그린다. apex 가 None 이면 지운다. 선은 가려도 보이게 그린다.
        """
        empty = marker_mesh(np.zeros((0, 3)), [], np.zeros(0))
        if apex is None:
            self._camera = self._drone = empty
        else:
            apex, corners = np.asarray(apex, float), np.asarray(corners, float)
            use_model = self._drone_model is not None and body is not None
            self._camera = camera_mesh(apex, corners, rgb, pyramid=not use_model)
            self._drone = drone_mesh(*self._drone_model, apex, body, rgb) if use_model else empty
        self.update()

    def reset_view(self) -> None:
        """초기 시점으로 되돌린다 — 각도까지 함께."""
        if self._model is not None:
            self.camera.reset_orientation()
            self.camera.fit(self._model.lo, self._model.hi, self._aspect())
            self.update()

    def zoom_by(self, steps: float) -> None:
        """버튼 한 번 = 휠 한 칸. 휠과 같은 함수를 쓴다."""
        self.camera.zoom(steps)
        self.update()

    def top_view(self) -> None:
        """바로 위에서 내려다본다. 매핑 결과는 평면으로 확인할 일이 많다."""
        if self._model is None:
            return
        self.camera.look_down()
        self.camera.fit(self._model.lo, self._model.hi, self._aspect())
        self.update()

    def _aspect(self) -> float:
        return max(1e-6, self.width() / max(1, self.height()))

    # ---- GL 생명주기 ----

    def initializeGL(self) -> None:
        f = self.context().functions()
        f.glEnable(0x0B71)          # GL_DEPTH_TEST
        f.glDisable(0x0B44)         # GL_CULL_FACE — 사진측량 메시는 면 방향이
                                    # 일관되지 않아 컬링하면 구멍이 뚫린다.

        self._program = QOpenGLShaderProgram()
        ok = (self._program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERT_SRC)
              and self._program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, FRAG_SRC)
              and self._program.link())
        if not ok:
            raise RuntimeError(f"셰이더 실패:\n{self._program.log()}")

        self.context().aboutToBeDestroyed.connect(self.release_gl)

        if self._pending is not None:
            self._upload(self._pending)
            self._pending = None

    def release_gl(self) -> None:
        """GL 자원을 놓는다. **창을 닫을 때 페이지의 shutdown 이 먼저 부른다.**

        컨텍스트의 aboutToBeDestroyed 에만 맡기면 앱 종료 때 위젯(C++)이 먼저 지워진 뒤에 불려
        makeCurrent 가 RuntimeError 를 내고, 텍스처가 컨텍스트 없이 풀리며 경고가 수십 줄 찍힌다 (실측).
        두 번 불려도 되고, 위젯이 이미 지워졌으면 아무것도 하지 않는다.
        """
        if self._program is None or not shiboken6.isValid(self):
            return
        self.makeCurrent()
        self._release_parts()
        self._release(self._marker_part)
        self._release(self._camera_part)
        self._release(self._grid_part)
        self._release(self._drone_part)
        self._marker_part = self._camera_part = self._grid_part = self._drone_part = None
        self._program = None
        self.doneCurrent()

    # PySide6 의 glDrawElements 오프셋 인자는 shiboken6.VoidPtr 여야 한다.
    # int 0 은 ValueError, ctypes.c_void_p(0) 은 예외도 GL 오류도 없이
    # 조용히 아무것도 그리지 않는다. 실측으로 확인했다.
    _IBO_OFFSET_0 = shiboken6.VoidPtr(0)

    def paintGL(self) -> None:
        f = self.context().functions()
        r, g, b = rgbf(VIEWPORT_BG)
        f.glClearColor(r, g, b, 1.0)
        f.glClear(0x4000 | 0x0100)   # COLOR_BUFFER_BIT | DEPTH_BUFFER_BIT

        if not self._parts or self._program is None:
            return

        w = max(1, int(self.width() * self.devicePixelRatioF()))
        h = max(1, int(self.height() * self.devicePixelRatioF()))
        view = self.camera.view()
        mvp = self.camera.projection(w, h) * view

        self._program.bind()
        self._program.setUniformValue("u_mvp", mvp)
        self._program.setUniformValue("u_normal", view.normalMatrix())
        self._program.setUniformValue("u_light_dir", QVector3D(0.3, 0.4, 1.0).normalized())
        self._program.setUniformValue("u_tex", 0)

        for part in self._parts:
            self._draw(f, part)
        if self._grid is not None:
            self._grid_part = self._swap_overlay(self._grid_part, self._grid)
            self._grid = None
        if self._grid_part is not None:     # 바닥에 붙은 선 — 건물에 가리는 게 맞다
            self._draw(f, self._grid_part)
        if self._drone is not None:
            self._drone_part = self._swap_overlay(self._drone_part, self._drone)
            self._drone = None
        if self._drone_part is not None:    # 부피가 있는 메시 — 깊이 테스트가 없으면 뒷면이 앞을 덮는다
            self._draw(f, self._drone_part)

        # 새 오버레이는 그리는 자리(컨텍스트가 현재인 곳)에서 올린다
        # 핀은 화면 크기가 일정해야 해서 시점이 바뀔 때마다 다시 만든다 (수십 개라 매 프레임이어도 가볍다)
        if self._markers_dirty or len(self._marker_points):
            self._marker_part = self._swap_overlay(
                self._marker_part, marker_mesh(self._marker_points, self._marker_colors, self._pin_heights()))
            self._markers_dirty = False
        if self._camera is not None:
            self._camera_part = self._swap_overlay(self._camera_part, self._camera)
            self._camera = None
        f.glDisable(0x0B71)                 # GL_DEPTH_TEST — 가려도 보이게. 핀이 카메라 선 위에 온다
        for part in (self._camera_part, self._marker_part):
            if part is not None:
                self._draw(f, part)
        f.glEnable(0x0B71)
        self._program.release()
        self._label_layer.update()          # 시점이 바뀌었을 수 있다 — 라벨도 다시 그린다

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._label_layer.setGeometry(self.rect())

    def paint_labels(self, painter: QPainter) -> None:
        """격자 라벨 — 3D 자리를 투영해 칩 + 글자로. `_LabelLayer` 가 부른다."""
        if not self._grid_labels:
            return
        px = self.camera.project(np.array([p for p, _ in self._grid_labels]), self.width(), self.height())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont(self.font().family(), 12, QFont.Weight.Bold))
        fm = painter.fontMetrics()
        for (x, y), (_, text) in zip(px, self._grid_labels):
            if not np.isfinite(x):
                continue
            side = fm.height() + 4
            chip = QRectF(x - side / 2, y - side / 2, max(side, fm.horizontalAdvance(text) + 8), side)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(LABEL_BG)
            painter.drawRoundedRect(chip, 5, 5)
            painter.setPen(QColor(*GRID_RGB))
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)

    def _swap_overlay(self, old: _Part | None, sub: loader.SubMesh) -> _Part | None:
        """이전 오버레이를 놓고 새것을 올린다. 비었으면 None."""
        self._release(old)
        if not len(sub.indices):
            return None
        part = self._make_part(sub)
        # 1×N 색표 — 보간하면 이웃 색이 섞인다
        part.tex.setMinMagFilters(QOpenGLTexture.Filter.Nearest, QOpenGLTexture.Filter.Nearest)
        return part

    def _draw(self, f, part: _Part) -> None:
        part.tex.bind(0)
        part.vao.bind()
        f.glDrawElements(0x0004, part.count, 0x1405, self._IBO_OFFSET_0)  # TRIANGLES, UINT
        part.vao.release()


    def resizeGL(self, w: int, h: int) -> None:
        pass    # 투영은 paintGL 에서 매번 만든다

    # ---- 마우스 ----

    def mousePressEvent(self, e) -> None:
        self._last_pos = e.position()

    def mouseMoveEvent(self, e) -> None:
        if e.buttons() == Qt.MouseButton.NoButton:
            self._set_hover(self.marker_at(e.position().x(), e.position().y()))
            return
        self._set_hover(-1)                 # 돌리는 중에는 카드를 띄우지 않는다
        if self._last_pos is None:
            return
        d = e.position() - self._last_pos
        self._last_pos = e.position()
        panning = (e.buttons() & Qt.MouseButton.MiddleButton) or \
                  (e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if panning:
            self.camera.pan(d.x(), d.y(), self.height())
        elif e.buttons() & Qt.MouseButton.LeftButton:
            self.camera.orbit(d.x(), d.y())
        else:
            return
        self.update()

    def leaveEvent(self, e) -> None:
        super().leaveEvent(e)
        self._set_hover(-1)

    def marker_pos(self, i: int) -> QPointF | None:
        """i 번 핀 몸통 가운데의 위젯 픽셀. 화면 뒤면 None — 호버 카드를 핀에 붙일 때 쓴다."""
        if not 0 <= i < len(self._marker_points):
            return None
        mid = self._marker_points[i] + [0, 0, PIN_MID * self._pin_heights()[i]]
        x, y = self.camera.project(mid, self.width(), self.height())[0]
        return QPointF(x, y) if np.isfinite(x) else None

    def marker_at(self, x: float, y: float) -> int:
        """위젯 픽셀 (x, y) 에서 HOVER_PX 안의 가장 가까운 핀 번호. 없으면 -1. 핀 몸통 가운데로 잰다."""
        if not len(self._marker_points):
            return -1
        mid = self._marker_points.copy()
        mid[:, 2] += PIN_MID * self._pin_heights()
        px = self.camera.project(mid, self.width(), self.height())
        d = np.hypot(px[:, 0] - x, px[:, 1] - y)
        d[~np.isfinite(d)] = np.inf
        i = int(np.argmin(d))
        return i if d[i] <= HOVER_PX else -1

    def _set_hover(self, i: int) -> None:
        if i != self._hover:
            self._hover = i
            self.marker_hovered.emit(i)

    def mouseReleaseEvent(self, e) -> None:
        self._last_pos = None

    def wheelEvent(self, e) -> None:
        self.camera.zoom(e.angleDelta().y() / 120.0)
        self.update()

    # ---- 내부 ----

    def _upload(self, model: loader.Model) -> None:
        # attribute 설정은 프로그램이 바인딩된 상태여야 한다.
        self._program.bind()
        self._parts.extend(self._make_part(sub) for sub in model.parts)
        self._program.release()

    def _make_part(self, sub: loader.SubMesh) -> _Part:
        """SubMesh 하나 → GL 자원. 프로그램이 바인딩된 상태에서 부른다."""
        part = _Part()
        part.vao.create()
        part.vao.bind()

        # 위치·법선·UV 를 하나의 인터리브 버퍼로 올린다.
        interleaved = np.hstack([sub.positions, sub.normals, sub.uvs]).astype(np.float32)
        part.vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        part.vbo.create(); part.vbo.bind()
        part.vbo.allocate(interleaved.tobytes(), interleaved.nbytes)

        stride = 8 * 4
        prog = self._program
        for loc, size, offset in ((0, 3, 0), (1, 3, 12), (2, 2, 24)):
            prog.enableAttributeArray(loc)
            prog.setAttributeBuffer(loc, 0x1406, offset, size, stride)  # GL_FLOAT

        part.ibo = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
        part.ibo.create(); part.ibo.bind()
        part.ibo.allocate(sub.indices.tobytes(), sub.indices.nbytes)
        part.count = len(sub.indices)

        part.vao.release()
        part.vbo.release()
        part.ibo.release()

        h, w, _ = sub.texture.shape
        buf = sub.texture.tobytes()      # QImage 가 참조하므로 살려둔다
        img = QImage(buf, w, h, w * 3, QImage.Format.Format_RGB888)
        part.tex = QOpenGLTexture(img)   # 기본값이 밉맵 생성이다
        part.tex.setMinificationFilter(QOpenGLTexture.Filter.LinearMipMapLinear)
        part.tex.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
        part.tex.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        return part

    def _release_parts(self) -> None:
        for p in self._parts:
            self._release(p)
        self._parts.clear()

    @staticmethod
    def _release(p: _Part | None) -> None:
        if p is None:
            return
        if p.tex is not None:
            p.tex.destroy()
        if p.vbo is not None:
            p.vbo.destroy()
        if p.ibo is not None:
            p.ibo.destroy()
        p.vao.destroy()


class _LabelLayer(QWidget):
    """뷰포트 위의 투명한 자식 — 격자 라벨만 그린다. 마우스는 아래 뷰포트로 흘린다.

    paintGL 안에서 QPainter 로 덧칠하면 화면에는 나와도 `grab()` 에는 빠지고(실측), GL 상태도
    흐트러진다. 자식 위젯은 Qt 가 GL 화면 위에 합성하므로 어디서나 똑같이 보인다.
    """

    def __init__(self, view: MeshViewport) -> None:
        super().__init__(view)
        self._view = view
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, e) -> None:
        painter = QPainter(self)
        self._view.paint_labels(painter)
        painter.end()


PIN_PX = 28         # 핀 높이 [화면 픽셀] — 줌과 무관하게 이 크기로 보인다
PIN_ASPECT = 0.3    # 핀 가운데 반폭 ÷ 높이
PIN_MID = 0.6       # 가장 넓은 곳의 높이 비율 — 호버 판정도 여기를 잰다


def marker_mesh(points: np.ndarray, colors: list[tuple[int, int, int]], heights: np.ndarray) -> loader.SubMesh:
    """점마다 마름모 핀 (아래 끝이 점, 위 끝이 heights 위). 색은 1×N 텍스처의 칸 하나씩.

    높이를 점마다 따로 받는다 — 뷰포트가 화면 크기를 일정하게 맞춘 값을 준다 (`_pin_heights`).
    Z-up 을 가정한다 (W · 카메라 모두 Z-up).
    """
    # 8면체 (높이 1 기준): 아래 꼭짓점 0, 가운데 4, 위 5
    w = PIN_ASPECT
    unit = np.array([[0, 0, 0], [w, 0, PIN_MID], [0, w, PIN_MID], [-w, 0, PIN_MID],
                     [0, -w, PIN_MID], [0, 0, 1]], float)
    tri = np.array([[0, 2, 1], [0, 3, 2], [0, 4, 3], [0, 1, 4],
                    [5, 1, 2], [5, 2, 3], [5, 3, 4], [5, 4, 1]])
    n = len(points)
    pos = (points[:, None, :] + unit[None] * np.asarray(heights, float).reshape(-1, 1, 1)).reshape(-1, 3)
    nrm = np.tile(unit - [0, 0, PIN_MID], (n, 1))      # 모양이 닮은꼴이라 법선은 높이와 무관하다
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    uv = np.repeat(np.c_[(np.arange(n) + 0.5) / max(n, 1), np.full(n, 0.5)], len(unit), 0)
    idx = (tri[None] + (np.arange(n) * len(unit))[:, None, None]).ravel()
    tex = np.array(colors, np.uint8).reshape(1, -1, 3) if n else np.zeros((1, 1, 3), np.uint8)
    return loader.SubMesh(pos.astype(np.float32), nrm.astype(np.float32), uv.astype(np.float32),
                          idx.astype(np.uint32), np.ascontiguousarray(tex))


CAM_BODY = 0.06     # 카메라 몸통(사각뿔) 길이 [m]
CAM_LINE_W = 0.005  # 시야선 · 촬영 범위 굵기 [m]. macOS Core 프로필은 GL 선이 1px 고정이라 띠로 그린다


def camera_mesh(apex: np.ndarray, corners: np.ndarray, rgb: tuple[int, int, int],
                pyramid: bool = True) -> loader.SubMesh:
    """드론 카메라 한 대 — 시야선 4개 + 촬영 범위 테두리 (+ pyramid 면 apex 의 작은 사각뿔).

    선은 서로 직교하는 띠 두 장(십자 단면)이라 어느 방향에서 봐도 굵기가 남는다.
    """
    dirs = corners - apex
    dirs /= np.maximum(np.linalg.norm(dirs, axis=1, keepdims=True), 1e-12)
    base = apex + CAM_BODY * dirs
    tris = []
    if pyramid:
        tris += [(apex, base[i], base[(i + 1) % 4]) for i in range(4)]
        tris += [(base[0], base[1], base[2]), (base[0], base[2], base[3])]
    for a, b in [(base[i], corners[i]) for i in range(4)] + [(corners[i], corners[(i + 1) % 4]) for i in range(4)]:
        tris += _ribbon(a, b, CAM_LINE_W)
    return _flat_mesh(tris, rgb)


def drone_mesh(tri: np.ndarray, normals: np.ndarray, at: np.ndarray, body: np.ndarray,
               rgb: tuple[int, int, int]) -> loader.SubMesh:
    """기체 좌표 삼각형 (F,3,3) 을 자세 body(3,3) 로 돌려 at 에 놓은 한 색 메시. 법선은 회전만 받는다."""
    pos = tri.reshape(-1, 3) @ body.T + at
    nrm = np.repeat(normals @ body.T, 3, axis=0)
    uv = np.full((len(pos), 2), 0.5, np.float32)
    tex = np.array(rgb, np.uint8).reshape(1, 1, 3)
    return loader.SubMesh(pos.astype(np.float32), nrm.astype(np.float32), uv,
                          np.arange(len(pos), dtype=np.uint32), np.ascontiguousarray(tex))


def _flat_mesh(tris: list[tuple], rgb: tuple[int, int, int]) -> loader.SubMesh:
    """삼각형 목록 → 한 색 SubMesh. 삼각형마다 꼭짓점을 따로 둔다 (면 법선 = 각진 음영)."""
    pos = np.array(tris, float).reshape(-1, 3)
    t = pos.reshape(-1, 3, 3)
    nrm = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    nrm = np.repeat(nrm, 3, axis=0)
    uv = np.full((len(pos), 2), 0.5)
    tex = np.array(rgb, np.uint8).reshape(1, 1, 3)
    return loader.SubMesh(pos.astype(np.float32), nrm.astype(np.float32), uv.astype(np.float32),
                          np.arange(len(pos), dtype=np.uint32), np.ascontiguousarray(tex))


def _ribbon(a: np.ndarray, b: np.ndarray, width: float) -> list[tuple]:
    """선분 a→b 를 십자 단면의 띠 두 장(삼각형 4개)으로."""
    u = b - a
    u = u / max(np.linalg.norm(u), 1e-12)
    ref = np.array([0.0, 0, 1]) if abs(u[2]) < 0.9 else np.array([1.0, 0, 0])
    p1 = np.cross(u, ref)
    p1 /= np.linalg.norm(p1)
    p2 = np.cross(u, p1)
    out = []
    for p in (p1 * width / 2, p2 * width / 2):
        out += [(a - p, a + p, b + p), (a - p, b + p, b - p)]
    return out


def configure_surface_format() -> None:
    """QApplication 생성 **전에** 호출해야 한다. macOS 는 3.2+ Core 아니면 2.1 로 떨어진다."""
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)
