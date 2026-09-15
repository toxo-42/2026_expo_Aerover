"""GLB → OpenGL 업로드용 배열.

ODM 산출물은 텍스처 아틀라스가 여러 장으로 쪼개져 있어, 서브메시마다
자기 텍스처를 가진다. UV 가 각자 0~1 범위라 하나로 합칠 수 없다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh


@dataclass
class SubMesh:
    """드로우콜 하나에 대응. 배열은 전부 GL 이 바로 받는 dtype 이다."""
    positions: np.ndarray   # (N, 3) float32
    normals: np.ndarray     # (N, 3) float32
    uvs: np.ndarray         # (N, 2) float32
    indices: np.ndarray     # (M*3,) uint32
    texture: np.ndarray     # (H, W, 3) uint8


@dataclass
class Model:
    parts: list[SubMesh]
    lo: np.ndarray          # (3,) float32 — 바운딩 박스 최소 (카메라 fit 용)
    hi: np.ndarray          # (3,) float32 — 바운딩 박스 최대
    load_sec: float

    @property
    def vertex_count(self) -> int:
        return sum(len(p.positions) for p in self.parts)

    @property
    def face_count(self) -> int:
        return sum(len(p.indices) // 3 for p in self.parts)


def load(path: str | Path) -> Model:
    t0 = time.perf_counter()
    scene = trimesh.load(path, process=False)

    if isinstance(scene, trimesh.Trimesh):
        meshes = [scene]
    else:
        # ODM 산출물의 노드 변환은 전부 identity 였다. 그렇지 않은 파일이
        # 들어오면 조용히 틀리는 대신 알린다.
        for node in scene.graph.nodes_geometry:
            T, _ = scene.graph[node]
            if not np.allclose(T, np.eye(4)):
                raise NotImplementedError(
                    f"노드 변환이 identity 가 아니다: {node}. 씬 그래프 처리 필요")
        meshes = list(scene.geometry.values())

    parts = [_to_submesh(m) for m in meshes]
    if not parts:
        raise ValueError(f"지오메트리가 없다: {path}")

    lo = np.min([p.positions.min(axis=0) for p in parts], axis=0)
    hi = np.max([p.positions.max(axis=0) for p in parts], axis=0)

    return Model(parts, lo.astype(np.float32), hi.astype(np.float32),
                 time.perf_counter() - t0)


def _to_submesh(m: trimesh.Trimesh) -> SubMesh:
    visual = m.visual
    uv = getattr(visual, "uv", None)
    if uv is None:
        raise ValueError("UV 가 없는 메시 — 텍스처를 입힐 수 없다")

    img = visual.material.baseColorTexture
    tex = np.asarray(img.convert("RGB"), dtype=np.uint8)
    # OpenGL 텍스처 원점은 좌하단, glTF 는 좌상단. 세로로 뒤집어 맞춘다.
    tex = np.ascontiguousarray(tex[::-1])

    return SubMesh(
        positions=np.ascontiguousarray(m.vertices, dtype=np.float32),
        normals=np.ascontiguousarray(m.vertex_normals, dtype=np.float32),
        uvs=np.ascontiguousarray(uv, dtype=np.float32),
        indices=np.ascontiguousarray(m.faces.ravel(), dtype=np.uint32),
        texture=tex,
    )
