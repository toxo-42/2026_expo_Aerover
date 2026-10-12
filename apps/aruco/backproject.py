"""탐지 픽셀 → 시연 좌표계 W 의 3D 점. 광선을 (W 로 옮긴) 3D 맵 메시와 교차시킨다.

    픽셀 (YOLO 박스의 한 점)
      → 왜곡을 풀어 카메라 광선        undistortPoints — **반복 50회** (아래)
      → pose.py 의 자세로 W 광선       d_W = R^T · d_cam,  원점 = C
      → 메시 삼각형과 교차, 가장 가까운 것   Möller–Trumbore
      → W 의 (x, y, z) [m]. 아무것도 안 맞으면 NaN

바닥 평면(Z=0)이 아니라 메시와 교차시키는 이유: 요구조자가 건물 2~3층 · 옥상 · 잔해 위에 있다.
평면과 교차시키면 그들은 전부 광선을 따라 바닥까지 밀려 엉뚱한 자리에 찍힌다.

**메시는 이미 W 로 옮겨져 있어야 한다** (align.py 의 apply 로 정점을 변환). 이 모듈은 좌표계를 모른다.
순수 numpy 라 aerover 로 옮길 때 GLB 로더(trimesh)만 붙이면 된다.

## undistortPoints 는 반복 횟수를 반드시 준다

기본값(5회)이면 이 카메라(k1 ≈ -0.49)에서 화면 구석 픽셀이 22px 틀린 광선이 된다
(test_calibrate.py 에서 찾았다). 구석에 걸린 요구조자가 수 cm 밀린다.
"""
from __future__ import annotations

import cv2
import numpy as np

from pose import Pose

CRIT = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 50, 1e-10)
EPS = 1e-12                 # 광선과 거의 평행한 삼각형은 건너뛴다


def pixel_rays(uv: np.ndarray, pose: Pose, K: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """픽셀 (N,2) → W 광선 방향 (N,3), 단위벡터. 원점은 모두 pose.C."""
    x = cv2.undistortPoints(np.asarray(uv, np.float64).reshape(-1, 1, 2), K, dist, criteria=CRIT)
    d = np.hstack([x.reshape(-1, 2), np.ones((len(x), 1))]) @ pose.R      # (R^T d)^T = d^T R
    return d / np.linalg.norm(d, axis=1, keepdims=True)


class Mesh:
    """교차 계산용 삼각형 묶음. 삼각형마다 꼭짓점 하나와 두 변을 미리 계산해 둔다."""

    def __init__(self, vertices: np.ndarray, faces: np.ndarray) -> None:
        v = np.asarray(vertices, np.float64)
        f = np.asarray(faces, np.int64)
        self.v0 = v[f[:, 0]]
        self.e1 = v[f[:, 1]] - self.v0
        self.e2 = v[f[:, 2]] - self.v0

    def intersect(self, origin: np.ndarray, dirs: np.ndarray) -> np.ndarray:
        """광선마다 가장 가까운 교점의 거리 (N,). 안 맞으면 inf.

        광선 하나에 삼각형 전부를 한 번에 계산한다 — 23만 개에 수십 ms.
        탐지는 프레임당 몇 개뿐이라 공간 분할(BVH) 없이 충분하다.
        """
        o = np.asarray(origin, np.float64)
        s = o - self.v0                                   # 광선 원점 기준 — 광선마다 같다
        q = np.cross(s, self.e1)
        out = np.full(len(dirs), np.inf)
        for k, d in enumerate(np.asarray(dirs, np.float64)):
            p = np.cross(d, self.e2)
            det = np.einsum("ij,ij->i", self.e1, p)
            ok = np.abs(det) > EPS                        # 뒷면도 맞힌다 (메시 법선 방향을 믿지 않는다)
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            u = np.einsum("ij,ij->i", s, p) * inv
            v = (q @ d) * inv
            t = np.einsum("ij,ij->i", self.e2, q) * inv
            hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)
            if hit.any():
                out[k] = t[hit].min()
        return out


def backproject(uv: np.ndarray, pose: Pose, K: np.ndarray, dist: np.ndarray, mesh: Mesh) -> np.ndarray:
    """픽셀 (N,2) → W 점 (N,3). 메시에 안 맞은 광선은 NaN 행."""
    d = pixel_rays(uv, pose, K, dist)
    t = mesh.intersect(pose.C, d)
    pts = pose.C + t[:, None] * d
    pts[~np.isfinite(t)] = np.nan
    return pts
