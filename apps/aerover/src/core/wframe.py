"""시연 좌표계 W — aruco 와의 연결점 (README "aerover 와의 접점").

W 는 판 모서리 마커가 정한 고정 좌표계다 (미터, Z-up, 판 윗면 ≈ Z=0, 판은 0~1.2 × 0~1.2).
자세 추정 · 역투영 코드는 `apps/aruco` 에 있고 **aerover 는 이 파일에서만 가져다 쓴다.**
aruco 쪽 import 는 `pose` → `align` (cv2 · numpy 만), `backproject`, `align_model` 뿐이라
aerover 의 의존성으로 그대로 돈다.

3D 맵 파일(`model.glb` · `model_cropped.glb`)은 **늘 ODM 좌표로 저장한다.**
W 로는 쓸 때 옮긴다 — 회차 폴더의 `to_W.json` 행렬 하나로:

    X_W = M · [X_odm, 1]        M = s·R | t   (유사변환 — 회전 + 등방 축척 + 이동)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
import sys
from pathlib import Path
from typing import Callable

import numpy as np
import trimesh

from src.config import ARUCO_DIR, POSE_CAMERA_FILE, POSE_LAYOUT_FILE, TO_W_FILE
from src.gl.loader import Model, SubMesh

if str(ARUCO_DIR) not in sys.path:
    sys.path.insert(0, str(ARUCO_DIR))

import align_model as _align_model  # noqa: E402  (aruco)
import backproject as _bp  # noqa: E402  (aruco)
import pose as _pose        # noqa: E402  (aruco)

# ④ 배경 제거 상자 (W, 여유 포함 — cropper 에 pad=0 으로 준다).
# XY: 판은 1.2m 지만 배치표의 마커 바깥 코너가 0.021~1.226 까지 간다 (마커를 가장자리에 붙였다).
#     마커 전체를 1.4cm 여유로 감싼다. 받침대 윗면이 판과 같은 높이라 높이로는 경계가 안 갈린다.
# Z:  가장 낮은 마커 코너 -13mm → -3cm.  가장 높은 건물 53cm + 5cm — 000331_plus 에서 지붕이 W 로
#     55.7cm 까지 올라왔다 (정합 잔차 ~1cm + 메시 요철). +1cm 로는 지붕이 잘렸다.
CROP_LO = np.array([-0.02, -0.02, -0.03])
CROP_HI = np.array([1.24, 1.24, 0.58])

Pose = _pose.Pose
PoseEstimator = _pose.PoseEstimator
Mesh = _bp.Mesh


def load_estimator(camera: Path = POSE_CAMERA_FILE, layout: Path = POSE_LAYOUT_FILE) -> PoseEstimator:
    """카메라(기본: aruco 의 1280×960 체스보드) · 마커 배치표로 자세 추정기를 만든다. 코너는 SUBPIX."""
    return PoseEstimator(*_pose.load_camera(camera), _pose.load_layout(layout))


# ---- to_W ----

def has_to_w(model_dir: Path) -> bool:
    return (model_dir / TO_W_FILE).is_file()


def load_to_w(model_dir: Path) -> np.ndarray:
    """회차 폴더 → ODM→W 4x4."""
    return np.array(json.loads((model_dir / TO_W_FILE).read_text())["matrix"], float)


# 자동 정합을 받아들이는 잔차 중앙값 상한. 정상 회차는 2~3mm (perfect_ODM 2.3 · 20260922_234609 2.2).
# ④ 상자의 여유가 1.4cm 라 이보다 크게 틀린 행렬로 자르면 판 가장자리가 잘린다 —
# 그럴 바엔 to_W.json 을 만들지 않고 평면도(수동)로 남긴다. 배치표가 판과 다를 때 이렇게 된다.
MAX_ALIGN_MEDIAN_MM = 10.0


@dataclass
class AlignSummary:
    corners: int            # 쓴 마커 코너 수
    markers: list[int]
    median_mm: float
    max_mm: float


def can_align(model_dir: Path) -> bool:
    """정합에 필요한 ODM 산출물(`odm.ALIGN_ASSETS`)이 있나."""
    return all((model_dir / f).is_file() for f in ("shots.geojson", "cameras.json"))


def make_to_w(model_dir: Path, images: Path | None = None,
              progress: Callable[[int, int], None] | None = None) -> AlignSummary:
    """마커를 삼각측량해 `to_W.json` 을 만든다 (aruco/align_model.py 와 같은 계산).

    사진은 기본으로 회차의 `source.txt` 가 가리키는 원본이다. 마커가 모자라거나 잔차가
    `MAX_ALIGN_MEDIAN_MM` 를 넘으면 파일을 쓰지 않고 ValueError.
    """
    images = images or _align_model.source_images(model_dir)
    r = _align_model.compute(model_dir, images, POSE_LAYOUT_FILE, progress=progress)
    med = float(np.median(r.residual_mm))
    if med > MAX_ALIGN_MEDIAN_MM:
        raise ValueError(f"정합 잔차 중앙 {med:.1f}mm > {MAX_ALIGN_MEDIAN_MM:.0f}mm — "
                         f"배치표(markers_W.json)가 지금 판과 맞는지 확인. to_W.json 은 만들지 않았다")
    (model_dir / TO_W_FILE).write_text(json.dumps(r.to_json(POSE_LAYOUT_FILE.name), indent=2, ensure_ascii=False))
    return AlignSummary(len(r.keys), sorted({i for i, _ in r.keys}), med, float(r.residual_mm.max()))


def apply(M: np.ndarray, xyz: np.ndarray) -> np.ndarray:
    return np.asarray(xyz, float) @ M[:3, :3].T + M[:3, 3]


# ---- 메시 ----

def load_mesh_w(glb: Path, M: np.ndarray) -> Mesh:
    """GLB → W 로 옮긴 역투영용 삼각형 묶음. 서브메시(텍스처 조각)를 하나로 합친다."""
    scene = trimesh.load(str(glb), process=False)
    if isinstance(scene, trimesh.Trimesh):
        scene = trimesh.Scene(scene)
    verts, faces, base = [], [], 0
    for node in scene.graph.nodes_geometry:
        T, gname = scene.graph[node]
        g = scene.geometry[gname]
        verts.append(trimesh.transform_points(g.vertices, T))
        faces.append(g.faces + base)
        base += len(g.vertices)
    return Mesh(apply(M, np.vstack(verts)), np.vstack(faces))


def model_to_w(model: Model, M: np.ndarray) -> Model:
    """화면용 모델(`gl.loader`)을 W 로 옮긴 사본. 법선은 회전만 받는다 (축척은 등방이라 방향이 안 변한다)."""
    R = M[:3, :3] / np.cbrt(np.linalg.det(M[:3, :3]))
    parts = []
    for p in model.parts:
        n = p.normals @ R.T
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        parts.append(SubMesh(np.ascontiguousarray(apply(M, p.positions), np.float32),
                             np.ascontiguousarray(n, np.float32), p.uvs, p.indices, p.texture))
    lo = np.min([p.positions.min(0) for p in parts], 0)
    hi = np.max([p.positions.max(0) for p in parts], 0)
    return Model(parts, lo, hi, model.load_sec)


# ---- 탐지용 3D 맵 ----

MAP_FILES = ("model_cropped.glb", "model.glb")     # 자른 것이 있으면 그것 (mapping/page.py 의 ④ · ③ 산출물)


def map_file(model_dir: Path) -> Path | None:
    return next((model_dir / n for n in MAP_FILES if (model_dir / n).is_file()), None)


def list_maps(models_dir: Path) -> list[Path]:
    """탐지에 쓸 수 있는 회차 — `to_W.json` 과 모델이 둘 다 있는 것. 최근 것이 앞."""
    if not models_dir.is_dir():
        return []
    return sorted((d for d in models_dir.iterdir()
                   if d.is_dir() and has_to_w(d) and map_file(d)), key=lambda d: d.name, reverse=True)


# ---- 탐지 → W ----

FRAME_SIZE = (1280, 960)    # camera_1280x960.json 을 잰 해상도. 다르면 K 가 틀린다
MIN_MARKERS = 2             # 마커 하나는 평면 모호성으로 각도가 튄다 (pose.py 머리말)


# 메시를 빗나간 모서리 광선의 최대 거리 [m]. 비스듬히 보면 바닥(Z=0)과 수 km 밖에서 만난다 —
# 판(1.2m) 건너편 모서리까지 닿을 만큼만 둔다 (재생 사진은 판 중심에서 ~1.5m).
FOOTPRINT_FAR = 2.5
# 화면 네 모서리 — 좌상 · 우상 · 우하 · 좌하 (촬영 범위 테두리를 이 순서로 잇는다)
FRAME_CORNERS = np.array([[0, 0], [FRAME_SIZE[0] - 1, 0],
                          [FRAME_SIZE[0] - 1, FRAME_SIZE[1] - 1], [0, FRAME_SIZE[1] - 1]], float)


@dataclass
class CameraView:
    """맵에 그릴 드론 카메라 — 어디서(C) 어디를(footprint) 보고 있나."""
    C: np.ndarray           # W 카메라 위치 (3,)
    footprint: np.ndarray   # (4,3) 화면 모서리(FRAME_CORNERS 순서)가 닿는 W 점
    R: np.ndarray           # (3,3) W → 카메라 회전 (행 = 카메라 x · y(아래) · z(앞) 축의 W 방향)


def drone_body(R: np.ndarray) -> np.ndarray:
    """카메라 회전 → 맵에 그릴 기체 자세 (3,3, 열 = 기체 X · Y(앞) · Z(위) 의 W 방향).

    **기수(yaw)만 따르고 수평으로 둔다.** 카메라가 기체에 몇 도 기울어 달렸는지 아직 몰라서
    카메라 기울기를 기체 기울기로 옮기면 틀린다. 앞 = 카메라 시선의 수평 성분.
    거의 똑바로 내려다보면 시선의 수평 성분이 없으니 영상 위쪽(-y 축)을 앞으로 본다.
    """
    fwd = R[2] * [1, 1, 0]
    if np.linalg.norm(fwd) < 0.2:
        fwd = -R[1] * [1, 1, 0]
    y = fwd / max(np.linalg.norm(fwd), 1e-12)
    z = np.array([0.0, 0.0, 1.0])
    return np.column_stack([np.cross(y, z), y, z])


@dataclass
class Located:
    """프레임 한 장의 위치 결과."""
    markers: int                    # 검출된 마커 수
    camera: CameraView | None       # None = 자세 없음
    points: np.ndarray | None       # (N,3) W — 메시에 안 맞은 행은 NaN. 자세 없으면 None


def _land(C: np.ndarray, d: np.ndarray, t: np.ndarray) -> np.ndarray:
    """메시에 안 맞은 모서리 광선(t=inf) → 바닥면 Z=0. 위를 보거나 너무 멀면 FOOTPRINT_FAR 거리."""
    t = t.copy()
    miss = ~np.isfinite(t)
    down = miss & (d[:, 2] < -1e-9)
    t[down] = np.minimum(-C[2] / d[down, 2], FOOTPRINT_FAR)
    t[miss & ~down] = FOOTPRINT_FAR
    return C + t[:, None] * d


def locate(gray: np.ndarray, uv: np.ndarray, est: PoseEstimator, mesh: Mesh) -> Located:
    """프레임 한 장 + 픽셀들 → 카메라 위치 · 촬영 범위 · 픽셀마다의 W 점.

    자세를 못 풀면(마커 < MIN_MARKERS) camera · points 는 None 이다 — 틀린 자리에 찍느니 안 찍는다.
    박스 픽셀과 화면 모서리를 **한 번에** 메시에 쏜다 (교차 계산이 가장 비싸다).
    """
    h, w = gray.shape[:2]
    if (w, h) != FRAME_SIZE:
        raise ValueError(f"프레임 {w}x{h} — 캘리브레이션은 {FRAME_SIZE[0]}x{FRAME_SIZE[1]}")
    seen = est.detect(gray)
    pose = est.estimate(seen) if len(seen) >= MIN_MARKERS else None
    if pose is None:
        return Located(len(seen), None, None)
    uv = np.asarray(uv, float).reshape(-1, 2)
    d = _bp.pixel_rays(np.vstack([uv, FRAME_CORNERS]), pose, est.K, est.dist)
    t = mesh.intersect(pose.C, d)
    pts = pose.C + t[:, None] * d
    pts[~np.isfinite(t)] = np.nan
    n = len(uv)
    camera = CameraView(pose.C.copy(), _land(pose.C, d[n:], t[n:]), pose.R.copy())
    return Located(len(seen), camera, pts[:n])


# ---- 구역 격자 ----
# 판(1.2m)을 30cm 칸으로 — 열 A~D 는 +X, 행 1~4 는 +Y 방향. 좌표 대신 "B-3 구역" 으로 말한다.
# 맵 바닥에 같은 격자를 그려야 이름이 뜻을 갖는다 (`sector_grid`).
BOARD_M = 1.2
SECTOR_M = 0.3
SECTOR_COLS = "ABCD"
SECTOR_ROWS = 4
GROUND_Z = 0.05     # 이보다 낮으면 "지면" — 판 요철 · 피규어 발밑 높이


def sector(xyz) -> str | None:
    """W 점 → "B-3". 판 밖이면 None. 칸 경계는 위쪽 칸에 넣고, 판 끝(1.2)은 마지막 칸에 넣는다."""
    x, y = float(xyz[0]), float(xyz[1])
    if not (0 <= x <= BOARD_M and 0 <= y <= BOARD_M):
        return None
    col = min(int(x // SECTOR_M), len(SECTOR_COLS) - 1)
    row = min(int(y // SECTOR_M), SECTOR_ROWS - 1)
    return f"{SECTOR_COLS[col]}-{row + 1}"


def height_text(z: float) -> str:
    return "지면" if z < GROUND_Z else f"높이 {z:.2f}m"


def sector_grid(z: float = 0.012, label_off: float = 0.06) -> tuple[np.ndarray, list[tuple[np.ndarray, str]]]:
    """맵 바닥에 그릴 격자 — (선분 (N,2,3), [(라벨 자리, 글자)]).

    선은 판 윗면보다 12mm 띄운다 — 풀 텍스처 메시의 요철(~1cm)에 묻히지 않게. 잔해 · 건물에는 가린다.
    열 라벨(A~D)은 y=0 바깥, 행 라벨(1~4)은 x=0 바깥에 둔다.
    """
    ticks = np.arange(len(SECTOR_COLS) + 1) * SECTOR_M
    seg = [((t, 0, z), (t, BOARD_M, z)) for t in ticks] + [((0, t, z), (BOARD_M, t, z)) for t in ticks]
    mid = (np.arange(len(SECTOR_COLS)) + 0.5) * SECTOR_M
    labels = [(np.array([m, -label_off, z]), c) for m, c in zip(mid, SECTOR_COLS)]
    labels += [(np.array([-label_off, m, z]), str(i + 1)) for i, m in enumerate(mid)]
    return np.array(seg, float), labels


# ---- 요구조자 목록 ----
# 같은 객체도 **보는 방향에 따라 다른 점으로 찍힌다** — 박스 중앙이 가리키는 표면이 시점마다 달라서.
# 세션 20261004_130739 실측: 연속 프레임끼리는 0.5cm(자세 추정은 안정적)인데, 판 반대편에서 보면
# 4~6cm 벌어진다. 반경이 이보다 작으면 드론이 돌수록 같은 객체가 새로 생긴다 (3cm 일 때 요구조자 78명,
# 실제는 23~25명). 반경은 객체 크기에 맞춘다:
#   요구조자(누움) 8cm · 차량 12cm.
#   안전(서 있음) 12cm — 키가 커서 박스 중앙 광선이 가는 몸을 스쳐 **뒤쪽 바닥**에 맞는 일이 잦다
#   (몸통 z 3~6cm vs 바닥 z -1cm, 10~14cm 뒤). 8cm 면 15명, 12cm 면 9명 (실제 9, 피규어 간격은 18cm 이상).
# 이 판에서 요구조자 23 · 차량 5 · 안전 9 (MIN_HITS 10, tests/test_wframe 회귀).
MERGE_R: dict[str, float] = {"casualty": 0.08, "person": 0.12, "vehicle": 0.12}
MERGE_R_DEFAULT = 0.08
# 거리는 **수평으로** 잰다. 높이 차가 이보다 크면 다른 객체 — 건물의 위아래 층에 겹쳐 있는 요구조자
MERGE_DZ = 0.08


@dataclass(eq=False)       # 객체는 동일성으로 구분한다 — 필드 비교는 numpy 배열에서 모호하다
class Victim:
    cls: str
    xyz: np.ndarray     # W [m] — 관측 평균
    hits: int
    last: str           # 마지막으로 본 시각 (표시용)


class Victims:
    """프레임마다 오는 탐지를 객체 단위로 모은다. 드론이 한 바퀴 돌면 판 전체의 지도가 된다.

    관측이 들어와 평균이 움직이면, 이제 반경 안에 들어온 같은 클래스 객체를 **다시 합친다**.
    한 객체가 시점 편향으로 둘로 갈라졌다가 평균이 다가와도 둘로 남는 것을 막는다.
    합칠 때는 먼저 생긴 쪽에 모은다 — 화면의 번호(요구조자 #3)가 바뀌지 않게.
    """

    def __init__(self, radius: dict[str, float] | None = None, dz: float = MERGE_DZ) -> None:
        self.radius = MERGE_R if radius is None else radius
        self.dz = dz
        self.items: list[Victim] = []

    def _dist(self, a: np.ndarray, b: np.ndarray) -> float:
        """수평 거리. 높이 차가 dz 를 넘으면 무한대 (다른 층)."""
        return np.inf if abs(a[2] - b[2]) > self.dz else float(np.linalg.norm(a[:2] - b[:2]))

    def add(self, cls: str, xyz, stamp: str) -> Victim:
        p = np.asarray(xyz, float)
        r = self.radius.get(cls, MERGE_R_DEFAULT)
        same = [v for v in self.items if v.cls == cls and self._dist(v.xyz, p) <= r]
        if not same:
            self.items.append(Victim(cls, p, 1, stamp))
            return self.items[-1]
        v = min(same, key=lambda v: self._dist(v.xyz, p))
        v.xyz = (v.xyz * v.hits + p) / (v.hits + 1)
        v.hits += 1
        v.last = stamp
        return self._absorb(v, r)

    def _absorb(self, v: Victim, r: float) -> Victim:
        """v 가 움직였으니 반경 안에 들어온 같은 클래스를 합친다. 살아남는 것(먼저 생긴 쪽)을 돌려준다."""
        while True:
            u = next((u for u in self.items if u is not v and u.cls == v.cls
                      and self._dist(u.xyz, v.xyz) <= r), None)
            if u is None:
                return v
            keep, gone = (u, v) if self.items.index(u) < self.items.index(v) else (v, u)
            keep.xyz = (keep.xyz * keep.hits + gone.xyz * gone.hits) / (keep.hits + gone.hits)
            keep.hits += gone.hits
            keep.last = max(keep.last, gone.last)
            self.items.remove(gone)
            v = keep
