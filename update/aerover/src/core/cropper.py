"""배경(바닥·벽·주변) 제거.

남길 영역은 사람이 평면도에서 드래그로 고른다 (`ui/planview.py`, 콘티 3절).
자동화는 못 한다 — GPS 없이 재구성하면 ODM 좌표계는 회차마다 원점·축·스케일이
전부 임의라 경계값을 코드에 박을 수 없다. 자동화하려면 3차(ArUco→GCP)가 먼저다.

### 평면도 원본을 점군이 아니라 메시 정점에서 뽑는다

`odm_filterpoints/point_cloud.ply` 는 **NodeODM 의 all.zip 에 들어 있지 않다.** 대안인
`point_cloud.laz` 는 `laspy` 가 필요하고, 무엇보다 **자를 대상은 메시다.** 메시 정점을
쓰면 평면도와 크롭이 같은 좌표계임이 보장된다.
실측으로도 둘은 같은 프레임이었다 (메시 x -43~41 / LAZ x -66~48, LAZ 가 더 넓을 뿐).

### 높이는 z 가 아니라 "바닥 위 높이"다

1차-B 회차는 매트 평면이 z = -2.31 에 평평하게 놓여 있어 z > -2.6 한 줄로 바닥이
떨어졌다. **그 회차가 우연히 수평이었을 뿐이다.**
`20260906_full` 은 바닥이 **44.4° 기울어** 있었다 — 좌표계가 회차마다 임의라는 말은
원점·축척뿐 아니라 **어느 쪽이 위인지도 임의**라는 뜻이다.

그래서 바닥 평면을 최소자승으로 맞추고, 높이를 **그 평면으로부터의 잔차**로 잰다.
자르는 상자도 z 축이 아니라 **바닥에 평행한 슬랩**이 된다. 이러면 기울기와 무관하게
"바닥 위 몇 m 부터 몇 m 까지"가 회차를 가리지 않고 뜻이 통한다.

(평면 적합은 대상이 아니라 바닥이 지배한다 — 잔차 중앙값이 -0.08 로 0 에 붙었다.
 대상이 화면의 대부분을 차지하는 회차가 나오면 RANSAC 이 필요해진다.)

### 자르는 방식 — 삼각형을 썰지 않는다

**중심이 상자 안에 있는 삼각형만 통째로 남긴다.** 썰면 잘린 자리에 새 정점이 생기며
UV 가 깨진다. 통째로 남기면 원본 정점·UV 를 그대로 쓰므로 텍스처가 보존된다.
경계가 조금 삐져나오는 대신 텍스처가 온전하다 — 시각화 용도엔 이쪽이 맞다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import trimesh
from PIL import Image

PAD = 0.15          # 경계에 걸친 삼각형 여유
TEX_MAX = 1024      # 텍스처 최대 변 길이
UV_MARGIN = 0.005   # 텍스처를 자를 때 UV 경계 바깥 여유


@dataclass
class CropResult:
    faces_before: int
    faces_after: int
    out: Path

    @property
    def ratio(self) -> float:
        return self.faces_after / self.faces_before if self.faces_before else 0.0


# ---- 씬 · 바닥 평면 ----

def load_scene(path: str | Path) -> trimesh.Scene:
    loaded = trimesh.load(str(path))
    return loaded if isinstance(loaded, trimesh.Scene) else trimesh.Scene(loaded)


def world_vertices(scene: trimesh.Scene) -> np.ndarray:
    """노드 변환까지 먹인 전체 정점 (N,3). 평면도의 원본이다."""
    out = []
    for node in scene.graph.nodes_geometry:
        T, gname = scene.graph[node]
        out.append(trimesh.transform_points(scene.geometry[gname].vertices, T))
    return np.vstack(out) if out else np.zeros((0, 3))


def fit_plane(xyz: np.ndarray) -> np.ndarray:
    """바닥 평면 z = a·x + b·y + c 를 최소자승으로 맞춰 (a, b, c) 반환."""
    A = np.c_[xyz[:, 0], xyz[:, 1], np.ones(len(xyz))]
    coef, *_ = np.linalg.lstsq(A, xyz[:, 2], rcond=None)
    return coef


def height_above(xyz: np.ndarray, plane: np.ndarray | None) -> np.ndarray:
    """평면 위 높이. `plane` 이 없으면 z 를 그대로 쓴다."""
    if plane is None:
        return xyz[:, 2]
    return xyz[:, 2] - (plane[0] * xyz[:, 0] + plane[1] * xyz[:, 1] + plane[2])


def tilt_degrees(plane: np.ndarray) -> float:
    return float(np.degrees(np.arctan(np.hypot(plane[0], plane[1]))))


# ---- 상자 ----

@dataclass(frozen=True)
class CropBox:
    """x·y 는 월드 좌표, 세 번째 성분은 `plane` 이 있으면 바닥 위 높이, 없으면 z."""
    lo: np.ndarray
    hi: np.ndarray
    plane: np.ndarray | None = None

    def padded(self, pad: float) -> CropBox:
        return CropBox(np.asarray(self.lo, dtype=float) - pad,
                       np.asarray(self.hi, dtype=float) + pad, self.plane)

    def contains(self, points: np.ndarray) -> np.ndarray:
        """(N,3) 점마다 상자 안인지."""
        h = height_above(points, self.plane)
        return ((points[:, 0] >= self.lo[0]) & (points[:, 0] <= self.hi[0])
                & (points[:, 1] >= self.lo[1]) & (points[:, 1] <= self.hi[1])
                & (h >= self.lo[2]) & (h <= self.hi[2]))


def crop(scene: trimesh.Scene, out: str | Path,
         lo: np.ndarray, hi: np.ndarray, plane: np.ndarray | None = None,
         pad: float = PAD, tex_max: int = TEX_MAX,
         progress: Callable[[int, int], None] | None = None) -> CropResult:
    """상자로 메시를 잘라 GLB 로 내보낸다.

    `lo`/`hi` 의 세 번째 성분은 `plane` 이 있으면 **바닥 위 높이**, 없으면 z 다.
    `plane=None` 이면 축정렬 상자가 된다.
    """
    box = CropBox(lo, hi, plane).padded(pad)
    kept: dict[str, trimesh.Trimesh] = {}
    n_before = n_after = 0
    nodes = list(scene.graph.nodes_geometry)

    for i, node in enumerate(nodes):
        T, gname = scene.graph[node]
        geom = scene.geometry[gname]
        n_before += len(geom.faces)

        piece = crop_geometry(geom, T, box, tex_max)
        if piece is not None:
            kept[gname] = piece
            n_after += len(piece.faces)

        if progress:
            progress(i + 1, len(nodes))

    trimesh.Scene(kept).export(str(out))
    return CropResult(n_before, n_after, Path(out))


def crop_geometry(geom: trimesh.Trimesh, T: np.ndarray, box: CropBox,
                  tex_max: int = TEX_MAX) -> trimesh.Trimesh | None:
    """중심이 상자 안인 삼각형만 남긴 복사본. 하나도 안 남으면 None. 원본은 건드리지 않는다."""
    V = trimesh.transform_points(geom.vertices, T)
    centers = V[geom.faces].mean(axis=1)
    mask = box.contains(centers)
    if not mask.any():
        return None

    m = geom.copy()
    m.update_faces(mask)
    m.remove_unreferenced_vertices()
    m.apply_transform(T)
    shrink_texture(m, tex_max)
    return m


# ---- 텍스처 ----

def shrink_texture(m: trimesh.Trimesh, tex_max: int = TEX_MAX) -> None:
    """남은 삼각형이 실제로 쓰는 UV 영역만 남기고 텍스처를 잘라 재매핑한다.

    안 하면 전체 텍스처가 그대로 실려 파일이 몇 배로 커진다.
    trimesh 는 UV 원점을 **좌하단**으로 들고 있다 (glTF 를 읽고 쓸 때 v 를 뒤집는다).
    PIL 이미지는 행 0 이 위라서, v 를 뒤집어 픽셀 행으로 바꾼다.
    """
    vis = m.visual
    img = getattr(getattr(vis, "material", None), "baseColorTexture", None)
    uv = getattr(vis, "uv", None)
    if img is None or uv is None:
        return

    uv = np.asarray(uv, dtype=np.float64)
    (u0, v0), (u1, v1) = _uv_bounds(uv)
    W, H = img.size
    left, right = int(u0 * W), max(int(np.ceil(u1 * W)), int(u0 * W) + 1)
    top, bottom = int((1 - v1) * H), max(int(np.ceil((1 - v0) * H)), int((1 - v1) * H) + 1)

    crop_img = img.crop((left, top, right, bottom))
    if max(crop_img.size) > tex_max:
        crop_img.thumbnail((tex_max, tex_max), Image.LANCZOS)

    vis.uv = np.stack([(uv[:, 0] - u0) / max(u1 - u0, 1e-9),
                       (uv[:, 1] - v0) / max(v1 - v0, 1e-9)], 1)
    vis.material.baseColorTexture = crop_img


def _uv_bounds(uv: np.ndarray, margin: float = UV_MARGIN) -> tuple[np.ndarray, np.ndarray]:
    lo = np.clip(uv.min(0) - margin, 0, 1)
    hi = np.clip(uv.max(0) + margin, 0, 1)
    return lo, hi
