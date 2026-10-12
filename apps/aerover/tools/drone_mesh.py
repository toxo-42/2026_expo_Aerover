"""드론 STL → 맵에 그릴 작은 메시 (`models/drone_mesh.npz`). 한 번만 돌린다.

    uv run python -m tools.drone_mesh ~/Downloads/aerover_model.stl

원본은 출력용이라 면이 200만 개(103MB)다. 화면 표시에는 과해서 정점을 격자로 묶어 줄인다
(vertex clustering — 의존성 없이 numpy 로). 4mm 격자면 ~5만 면, 실루엣은 그대로다.

**원본 STL 은 위아래가 뒤집혀 있다.** X 축 둘레로 180° 돌린 것이 정상 자세다
(몸통이 위, 다리 · 레일이 아래). 저장하는 메시의 기체 좌표:

    +X 오른쪽, +Y 앞(몸통이 좁아지는 쪽 = 카메라), +Z 위, 단위 m, 원점 = 외곽 상자 중심
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import trimesh

from src.config import DRONE_MESH_FILE

CELL_MM = 4.0


def cluster_decimate(v: np.ndarray, f: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """같은 격자 칸의 정점을 평균 하나로 합친다. 찌그러진(꼭짓점이 겹친) 면 · 중복 면은 버린다."""
    _, inv = np.unique(np.floor(v / cell).astype(np.int64), axis=0, return_inverse=True)
    inv = inv.ravel()
    nv = np.zeros((inv.max() + 1, 3))
    np.add.at(nv, inv, v)
    nv /= np.bincount(inv)[:, None]
    nf = inv[f]
    nf = nf[(nf[:, 0] != nf[:, 1]) & (nf[:, 1] != nf[:, 2]) & (nf[:, 0] != nf[:, 2])]
    _, first = np.unique(np.sort(nf, axis=1), axis=0, return_index=True)
    return nv, nf[np.sort(first)]


def to_body(v_mm: np.ndarray) -> np.ndarray:
    """원본(뒤집힌 상태, mm) → 기체 좌표(m). X 둘레 180° = (x, -y, -z). 외곽 상자 중심을 원점으로."""
    v = np.asarray(v_mm, float) * [1, -1, -1] / 1000.0
    return v - (v.min(0) + v.max(0)) / 2


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="드론 STL 을 맵 표시용 메시로 줄인다")
    ap.add_argument("stl", type=Path)
    ap.add_argument("--cell", type=float, default=CELL_MM, help="정점을 묶는 격자 [mm]")
    ap.add_argument("--out", type=Path, default=DRONE_MESH_FILE)
    args = ap.parse_args(argv)

    m = trimesh.load(str(args.stl), force="mesh")
    v, f = cluster_decimate(m.vertices, m.faces, args.cell)
    v = to_body(v)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.out, vertices=v.astype(np.float32), faces=f.astype(np.uint32))
    print(f"면 {len(m.faces):,} → {len(f):,} · 크기 {np.ptp(v, 0).round(3)} m → {args.out}")


if __name__ == "__main__":
    main()
