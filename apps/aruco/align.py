"""ODM 좌표계 → 실측 좌표계 정합 (3차 ArUco→GCP).

**재처리 없이** 이미 있는 ODM 산출물만으로 좌표계를 옮긴다.

    all.zip
      ├─ odm_report/shots.geojson   장별 카메라 포즈 (rotation, translation)
      └─ cameras.json               내부 파라미터 (OpenSfM brown)

흐름:

    촬영본에서 ArUco 코너 검출
      → 여러 장의 관측을 삼각측량해 코너의 3D 위치 (ODM 좌표계)
      → 자로 잰 실측 좌표와 Umeyama 유사변환 (7-DoF: 축척·회전·이동)
      → 메시/점군에 그 변환을 걸면 미터 단위 고정 좌표계가 된다

같은 마커를 실시간 solvePnP 가 보므로 두 좌표계가 자동으로 맞물린다.

## 규약 (실측으로 검증함 — test_align.py 참조)

`shots.geojson` 의 `translation` 은 OpenSfM 의 t 벡터가 **아니라 카메라 위치 C** 다.
`reconstruction.json` 의 `-R^T·t` 와 1e-15 수준으로 일치함을 확인했다.

    Xc = R · (Xw - C)          R = Rodrigues(rotation)

정규화 좌표 → 픽셀 은 긴 변 기준이다. OpenCV 등가 카메라 행렬로 바꿔 쓴다:

    fx = fy = focal · max(W,H)
    cx = c_x · max(W,H) + W/2 - 0.5
    dist = (k1, k2, p1, p2, k3)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ARUCO_DICT = cv2.aruco.DICT_4X4_50      # Drone/run_mapping.py:36 과 같아야 한다


# ── 카메라 ────────────────────────────────────────────────────────────────

@dataclass
class Camera:
    """OpenSfM brown 카메라를 OpenCV 규약(K, dist)으로 들고 있는다."""
    width: int
    height: int
    K: np.ndarray
    dist: np.ndarray

    @classmethod
    def load(cls, cameras_json: str | Path) -> "Camera":
        d = json.loads(Path(cameras_json).read_text())
        c = next(iter(d.values()))
        if c["projection_type"] != "brown":
            raise ValueError(f"brown 모델만 지원한다 (받은 값: {c['projection_type']})")
        w, h = c["width"], c["height"]
        s = max(w, h)
        K = np.array([[c["focal_x"] * s, 0, c["c_x"] * s + w / 2 - 0.5],
                      [0, c["focal_y"] * s, c["c_y"] * s + h / 2 - 0.5],
                      [0, 0, 1.0]])
        dist = np.array([c["k1"], c["k2"], c["p1"], c["p2"], c["k3"]])
        return cls(w, h, K, dist)

    def project(self, Xw: np.ndarray, R: np.ndarray, C: np.ndarray):
        """월드 점 (N,3) → 픽셀 (N,2), 카메라 앞쪽 깊이 (N,)."""
        Xw = np.atleast_2d(Xw)
        rvec, _ = cv2.Rodrigues(R)
        uv, _ = cv2.projectPoints(Xw, rvec, (-R @ C).reshape(3, 1), self.K, self.dist)
        return uv.reshape(-1, 2), (R @ (Xw - C).T).T[:, 2]

    def undistort(self, uv: np.ndarray) -> np.ndarray:
        """픽셀 (N,2) → 왜곡 제거된 정규화 좌표 (N,2). 삼각측량의 입력이다."""
        p = np.asarray(uv, float).reshape(-1, 1, 2)
        return cv2.undistortPoints(p, self.K, self.dist).reshape(-1, 2)


# ── 포즈 ──────────────────────────────────────────────────────────────────

def load_shots(shots_geojson: str | Path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """파일명 → (R, C). R 은 월드→카메라 회전, C 는 카메라 위치."""
    g = json.loads(Path(shots_geojson).read_text())
    out = {}
    for f in g["features"]:
        p = f["properties"]
        R, _ = cv2.Rodrigues(np.array(p["rotation"], float))
        out[p["filename"]] = (R, np.array(p["translation"], float))
    return out


# ── 삼각측량 ──────────────────────────────────────────────────────────────

def triangulate(obs: list[tuple[np.ndarray, np.ndarray, np.ndarray]]) -> np.ndarray:
    """다시점 DLT. obs 는 (R, C, 정규화좌표(2,)) 목록. 3D 점 (3,) 반환.

    각 관측이 두 줄의 제약을 준다. 최소 2장, 실제로는 3장 이상을 권한다.
    """
    if len(obs) < 2:
        raise ValueError("삼각측량에는 관측이 2개 이상 필요하다")
    A = []
    for R, C, (x, y) in obs:
        P = np.hstack([R, (-R @ C).reshape(3, 1)])      # 3x4
        A.append(x * P[2] - P[0])
        A.append(y * P[2] - P[1])
    _, _, Vt = np.linalg.svd(np.array(A))
    X = Vt[-1]
    return X[:3] / X[3]


# ── 유사변환 ──────────────────────────────────────────────────────────────

def umeyama(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """src → dst 유사변환 (s, R, t).  dst ≈ s·R·src + t

    Umeyama 1991. 반사(det=-1)를 막는 보정이 들어 있다 — 없으면 좌표계가
    거울상으로 뒤집힌 해가 나올 수 있다.
    """
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    if len(src) < 3:
        raise ValueError("유사변환에는 대응점이 3개 이상 필요하다")
    mu_s, mu_d = src.mean(0), dst.mean(0)
    S, D = src - mu_s, dst - mu_d
    U, sig, Vt = np.linalg.svd(D.T @ S / len(src))
    W = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        W[2, 2] = -1
    R = U @ W @ Vt
    s = float(np.trace(np.diag(sig) @ W) * len(src) / (S ** 2).sum())
    return s, R, mu_d - s * R @ mu_s


def apply(s: float, R: np.ndarray, t: np.ndarray, X: np.ndarray) -> np.ndarray:
    return (s * (R @ np.atleast_2d(X).T)).T + t


# ── ArUco 검출 ────────────────────────────────────────────────────────────

def detect_corners(image_dir: str | Path, names: list[str] | None = None
                   ) -> dict[tuple[int, int], dict[str, np.ndarray]]:
    """촬영본에서 마커 코너를 뽑는다.

    반환: {(마커ID, 코너번호 0~3): {파일명: 픽셀(2,)}}
    코너 번호는 cv2 순서 — 마커 자체 방향 기준 좌상·우상·우하·좌하.
    """
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(ARUCO_DICT),
                                  cv2.aruco.DetectorParameters())
    d = Path(image_dir)
    files = sorted(d.glob("*.jpg")) if names is None else [d / n for n in names]
    out: dict[tuple[int, int], dict[str, np.ndarray]] = {}
    for f in files:
        img = cv2.imread(str(f))
        if img is None:
            continue
        corners, ids, _ = det.detectMarkers(img)
        if ids is None:
            continue
        for c, mid in zip(corners, ids.ravel()):
            for k, pt in enumerate(c.reshape(4, 2)):
                out.setdefault((int(mid), k), {})[f.name] = pt.astype(float)
    return out


def world_corners(center: np.ndarray, size: float, up: str) -> np.ndarray:
    """실측표 한 줄 → 코너 4개의 실측 좌표 (4,3).

    마커를 바닥에 평평하게, 변을 축과 나란히 붙였다고 본다 (준비 문서 3절).
    `up` 은 마커의 위쪽이 향하는 축 — "+Y", "-X" 등.
    순서는 cv2 와 같게 좌상·우상·우하·좌하로 맞춘다.
    """
    axes = {"+X": np.array([1., 0, 0]), "-X": np.array([-1., 0, 0]),
            "+Y": np.array([0, 1., 0]), "-Y": np.array([0, -1., 0])}
    if up not in axes:
        raise ValueError(f"up 은 {sorted(axes)} 중 하나여야 한다 (받은 값: {up})")
    u = axes[up]                                  # 마커 위쪽
    r = np.cross(u, np.array([0, 0, 1.]))         # 위에서 볼 때의 오른쪽
    h = size / 2
    c = np.asarray(center, float)
    return np.array([c - h * r + h * u, c + h * r + h * u,
                     c + h * r - h * u, c - h * r - h * u])
