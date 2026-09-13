"""텍스처 메시 뷰포트 — QOpenGLWidget.

ODM 산출물은 서브메시마다 텍스처가 달라 드로우콜을 파트 수만큼 낸다
(크롭본 45회). 이 규모에서는 문제되지 않는다.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import shiboken6

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QSurfaceFormat, QVector3D
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram,
                              QOpenGLTexture, QOpenGLVertexArrayObject)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

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


class MeshViewport(QOpenGLWidget):
    """마우스 조작: 좌드래그 회전 · 휠 줌 · 중클릭(또는 Shift+좌) 패닝."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.camera = OrbitCamera()
        self._model: loader.Model | None = None
        self._pending: loader.Model | None = None   # GL 컨텍스트 준비 전 대기분
        self._parts: list[_Part] = []
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

    def reset_view(self) -> None:
        """초기 시점으로 되돌린다 — 각도까지 함께."""
        if self._model is not None:
            self.camera.reset_orientation()
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

        self.context().aboutToBeDestroyed.connect(self._cleanup)

        if self._pending is not None:
            self._upload(self._pending)
            self._pending = None

    def _cleanup(self) -> None:
        """컨텍스트가 사라지기 전에 GL 자원을 놓는다."""
        self.makeCurrent()
        self._release_parts()
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
            part.tex.bind(0)
            part.vao.bind()
            f.glDrawElements(0x0004, part.count, 0x1405, self._IBO_OFFSET_0)  # TRIANGLES, UINT
            part.vao.release()
        self._program.release()


    def resizeGL(self, w: int, h: int) -> None:
        pass    # 투영은 paintGL 에서 매번 만든다

    # ---- 마우스 ----

    def mousePressEvent(self, e) -> None:
        self._last_pos = e.position()

    def mouseMoveEvent(self, e) -> None:
        if self._last_pos is None or e.buttons() == Qt.MouseButton.NoButton:
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

    def mouseReleaseEvent(self, e) -> None:
        self._last_pos = None

    def wheelEvent(self, e) -> None:
        self.camera.zoom(e.angleDelta().y() / 120.0)
        self.update()

    # ---- 내부 ----

    def _upload(self, model: loader.Model) -> None:
        # attribute 설정은 프로그램이 바인딩된 상태여야 한다.
        self._program.bind()
        for sub in model.parts:
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

            self._parts.append(part)
        self._program.release()

    def _release_parts(self) -> None:
        for p in self._parts:
            if p.tex is not None:
                p.tex.destroy()
            if p.vbo is not None:
                p.vbo.destroy()
            if p.ibo is not None:
                p.ibo.destroy()
            p.vao.destroy()
        self._parts.clear()


def configure_surface_format() -> None:
    """QApplication 생성 **전에** 호출해야 한다. macOS 는 3.2+ Core 아니면 2.1 로 떨어진다."""
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)
