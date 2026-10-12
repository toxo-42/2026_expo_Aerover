"""궤도(orbit) 카메라 — 대상을 중심에 두고 도는 방식.

ODM 좌표계는 Z 가 위다 (매트 평면이 z = -2.31 에 평평하게 놓였다).
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QMatrix4x4, QVector3D

UP = QVector3D(0.0, 0.0, 1.0)

DEFAULT_YAW_DEG = -60.0
DEFAULT_PITCH_DEG = 25.0


class OrbitCamera:
    def __init__(self) -> None:
        self.target = QVector3D(0.0, 0.0, 0.0)
        self.distance = 10.0
        self.yaw = math.radians(DEFAULT_YAW_DEG)      # z축 둘레 각도
        self.pitch = math.radians(DEFAULT_PITCH_DEG)  # 수평면에서 올려다본 각도
        self.fov_deg = 45.0
        self._min_dist = 1e-3

    # ---- 조작 ----

    def orbit(self, dx: float, dy: float, speed: float = 0.007) -> None:
        self.yaw -= dx * speed
        self.pitch += dy * speed
        limit = math.radians(89.0)          # 극점에서 up 벡터가 무너지는 것 방지
        self.pitch = max(-limit, min(limit, self.pitch))

    def zoom(self, steps: float, rate: float = 1.12) -> None:
        self.distance = max(self._min_dist, self.distance * (rate ** -steps))

    def pan(self, dx: float, dy: float, viewport_h: int) -> None:
        """화면 픽셀 이동량만큼 target 을 옮긴다. 거리에 비례해 감각이 일정하다."""
        scale = 2.0 * self.distance * math.tan(math.radians(self.fov_deg) / 2) / max(1, viewport_h)
        right, up = self._basis()
        self.target += right * (-dx * scale) + up * (dy * scale)

    def reset_orientation(self) -> None:
        self.yaw = math.radians(DEFAULT_YAW_DEG)
        self.pitch = math.radians(DEFAULT_PITCH_DEG)

    def look_down(self) -> None:
        """탑뷰 — 90도는 up 벡터가 무너져서 orbit 과 같은 한계각을 쓴다."""
        self.pitch = math.radians(89.0)

    def fit(self, lo, hi, aspect: float = 1.5, margin: float = 1.06) -> None:
        """바운딩 박스가 화면에 꽉 들어오도록 맞춘다.

        바운딩 스피어로 맞추면 납작한 모델(디오라마가 그렇다)이 작게 나온다.
        코너 8개를 현재 시선 기준으로 투영해 필요한 거리를 직접 구한다.
        ODM 좌표계는 회차마다 원점·축·스케일이 달라 이 자동 맞춤이 필수다.
        """
        lo = [float(v) for v in lo]
        hi = [float(v) for v in hi]
        self.target = QVector3D(*[(a + b) / 2 for a, b in zip(lo, hi)])

        tan_v = math.tan(math.radians(self.fov_deg) / 2)
        tan_h = tan_v * max(1e-6, aspect)
        right, up = self._basis()
        forward = (self.target - self.eye()).normalized()

        need = 0.0
        for i in range(8):
            corner = QVector3D(hi[0] if i & 1 else lo[0],
                               hi[1] if i & 2 else lo[1],
                               hi[2] if i & 4 else lo[2]) - self.target
            depth_off = QVector3D.dotProduct(corner, forward)
            for axis, tan_a in ((right, tan_h), (up, tan_v)):
                need = max(need, abs(QVector3D.dotProduct(corner, axis)) / tan_a - depth_off)

        diag = math.dist(lo, hi)
        self._min_dist = diag * 1e-3
        self.distance = max(self._min_dist, need * margin)

    # ---- 행렬 ----

    def eye(self) -> QVector3D:
        cp = math.cos(self.pitch)
        offset = QVector3D(cp * math.cos(self.yaw), cp * math.sin(self.yaw), math.sin(self.pitch))
        return self.target + offset * self.distance

    def view(self) -> QMatrix4x4:
        m = QMatrix4x4()
        m.lookAt(self.eye(), self.target, UP)
        return m

    def projection(self, w: int, h: int) -> QMatrix4x4:
        m = QMatrix4x4()
        near = max(1e-4, self.distance * 0.005)
        far = self.distance * 20.0 + 100.0
        m.perspective(self.fov_deg, w / max(1, h), near, far)
        return m

    def project(self, points: np.ndarray, w: int, h: int) -> np.ndarray:
        """3D 점 (N,3) → 위젯 픽셀 (N,2), 왼쪽 위 원점. 카메라 뒤의 점은 NaN.

        `paintGL` 과 같은 행렬이다 — 종횡비만 같으면 논리 픽셀로 넘겨도 된다.
        """
        m = self.projection(w, h) * self.view()
        M = np.array(m.data(), float).reshape(4, 4).T      # Qt 는 열 우선으로 내준다
        p = np.asarray(points, float).reshape(-1, 3)
        clip = np.c_[p, np.ones(len(p))] @ M.T
        out = np.full((len(p), 2), np.nan)
        ok = clip[:, 3] > 1e-9
        ndc = clip[ok, :2] / clip[ok, 3:4]
        out[ok] = np.c_[(ndc[:, 0] + 1) / 2 * w, (1 - ndc[:, 1]) / 2 * h]
        return out

    def world_per_px(self, points: np.ndarray, h: int) -> np.ndarray:
        """점 (N,3) 마다 화면 1픽셀이 차지하는 실제 길이 (N,). 화면에서 크기가 일정한 표시(핀)에 쓴다.

        원근 투영: 깊이 d 에서 화면 높이 전체 = 2·d·tan(fov/2). 깊이는 시선 방향 성분이다.
        """
        e, t = self.eye(), self.target
        eye = np.array([e.x(), e.y(), e.z()])
        fwd = np.array([t.x(), t.y(), t.z()]) - eye
        fwd /= max(np.linalg.norm(fwd), 1e-12)
        depth = np.maximum((np.asarray(points, float).reshape(-1, 3) - eye) @ fwd, 1e-6)
        return 2 * depth * math.tan(math.radians(self.fov_deg) / 2) / max(1, h)

    def _basis(self) -> tuple[QVector3D, QVector3D]:
        forward = (self.target - self.eye()).normalized()
        right = QVector3D.crossProduct(forward, UP).normalized()
        up = QVector3D.crossProduct(right, forward).normalized()
        return right, up
