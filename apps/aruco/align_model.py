"""ODM 회차 → 시연 좌표계 W 변환 (to_W.json). 3D 맵을 W 로 옮기는 마지막 조각.

    uv run python align_model.py ../aerover/3D_model/<회차>
    uv run python align_model.py <회차 폴더> --images <사진 폴더> --layout markers_W.json

흐름:

    회차 폴더의 shots.geojson (장별 카메라 자세) · cameras.json (ODM 이 추정한 내부값)
      → 원본 사진에서 마커 코너 검출 (marker_map 과 같은 APRILTAG — 치우침이 같아야 두 좌표가 맞는다)
      → 코너마다 여러 장의 관측을 삼각측량 → ODM 좌표계의 3D 코너 (재투영이 튀는 관측은 한 번 걸러 다시)
      → markers_W.json 의 같은 코너와 Umeyama 유사변환 → X_W = s·R·X_odm + t
      → <회차>/to_W.json (4x4 행렬 포함). aerover 는 model.glb 정점에 이 행렬을 곱하면 된다

aerover 는 ③ ODM 이 끝나면 `compute` 를 불러 같은 파일을 자동으로 만든다 (core/wframe.make_to_w).

`shots.geojson` 과 `model.glb` 는 같은 좌표계다 — 축은 2026-09-21 작업 faa3e3ab 로, 평행이동은
2026-09-22 회차 20260922_213623 에서 확인했다 (삼각측량한 마커 코너가 메시 표면에서 최대 2.9mm).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

import align
import pose as P

MIN_OBS = 3                 # 이보다 적은 장에 찍힌 코너는 쓰지 않는다
OUTLIER_PX = 3.0            # 재투영이 이보다 (그리고 중앙값 3배보다) 크면 그 관측을 버린다


def source_images(model_dir: Path) -> Path:
    """source.txt 는 aerover 루트 기준 상대경로다 (aerover/src/core/workspace.py)."""
    rel = (model_dir / "source.txt").read_text().strip()
    return (model_dir.parent.parent / rel).resolve()


def triangulate_corners(images: Path, cam: align.Camera, shots, layout,
                        progress: Callable[[int, int], None] | None = None) -> tuple[dict, dict]:
    """{(ID, 코너번호): ODM 3D 점}, {(ID, 코너번호): 쓴 관측 수}.

    `progress(done, total)` 는 사진 한 장을 읽을 때마다 불린다 — 폰 사진 111장에 약 140초라
    aerover 가 진행률을 보이고, 예외를 던져 중간에 멈출 수도 있다.
    """
    det = P.make_detector(cv2.aruco.CORNER_REFINE_APRILTAG)
    obs: dict[tuple[int, int], list[tuple[str, np.ndarray]]] = {}
    for n_done, name in enumerate(shots, 1):
        if progress is not None:
            progress(n_done, len(shots))
        img = cv2.imread(str(images / name), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        corners, ids, _ = det.detectMarkers(img)
        if ids is None:
            continue
        for c, i in zip(corners, ids.ravel()):
            if int(i) in layout:
                for k, pt in enumerate(c.reshape(4, 2)):
                    obs.setdefault((int(i), k), []).append((name, pt.astype(float)))

    points, used = {}, {}
    for key, lst in sorted(obs.items()):
        if len(lst) < MIN_OBS:
            continue
        px = np.array([p for _, p in lst])
        o = [(*shots[n], x) for (n, _), x in zip(lst, cam.undistort(px))]
        X = align.triangulate(o)
        uv = np.vstack([cam.project(X, *shots[n])[0] for n, _ in lst])
        e = np.linalg.norm(uv - px, axis=1)
        keep = e < max(OUTLIER_PX, 3 * np.median(e))
        if keep.sum() >= MIN_OBS and not keep.all():
            X = align.triangulate([oo for oo, k in zip(o, keep) if k])
        points[key], used[key] = X, int(keep.sum())
    return points, used


@dataclass
class ToW:
    """ODM → W 정합 결과. `to_json()` 이 to_W.json 내용이다."""
    s: float
    R: np.ndarray
    t: np.ndarray
    keys: list[tuple[int, int]]     # 쓴 (ID, 코너번호)
    residual_mm: np.ndarray         # keys 와 같은 순서
    used: dict[tuple[int, int], int]

    @property
    def matrix(self) -> np.ndarray:
        M = np.eye(4); M[:3, :3] = self.s * self.R; M[:3, 3] = self.t
        return M

    def to_json(self, layout_name: str) -> dict:
        return {"note": f"ODM 좌표 → W. X_W = s·R·X_odm + t  ({date.today().isoformat()}, 배치표 {layout_name})",
                "s": self.s, "R": self.R.tolist(), "t": self.t.tolist(), "matrix": self.matrix.tolist(),
                "corners": len(self.keys), "residual_median_mm": round(float(np.median(self.residual_mm)), 2),
                "residual_max_mm": round(float(self.residual_mm.max()), 2)}


def compute(model_dir: Path, images: Path, layout_file: Path = P.LAYOUT_FILE,
            progress: Callable[[int, int], None] | None = None) -> ToW:
    """회차 폴더 → 정합 결과. 파일은 쓰지 않는다. 입력이 모자라면 ValueError."""
    for f in ("shots.geojson", "cameras.json"):
        if not (model_dir / f).is_file():
            raise ValueError(f"{model_dir / f} 가 없다 — ③ ODM 이 ALIGN_ASSETS 를 저장한 회차여야 한다")
    cam = align.Camera.load(model_dir / "cameras.json")
    shots = align.load_shots(model_dir / "shots.geojson")
    layout = P.load_layout(layout_file)

    points, used = triangulate_corners(images, cam, shots, layout, progress)
    if len(points) < 3:
        raise ValueError(f"삼각측량한 코너가 {len(points)}개뿐이다 — 마커가 {MIN_OBS}장 이상에 찍혀야 한다")
    keys = sorted(points)
    src = np.array([points[k] for k in keys])
    dst = np.array([layout[i][c] for i, c in keys])
    s, R, t = align.umeyama(src, dst)
    res = np.linalg.norm(align.apply(s, R, t, src) - dst, axis=1) * 1000
    return ToW(s, R, t, keys, res, used)


def main() -> None:
    ap = argparse.ArgumentParser(description="ODM 회차 → 시연 좌표계 W 변환 (to_W.json)")
    ap.add_argument("model_dir", type=Path, help="aerover/3D_model/<회차> — shots.geojson · cameras.json 이 있는 곳")
    ap.add_argument("--images", type=Path, help="원본 사진 폴더 (기본: source.txt)")
    ap.add_argument("--layout", type=Path, default=P.LAYOUT_FILE)
    a = ap.parse_args()

    d = a.model_dir
    images = a.images or source_images(d)
    try:
        r = compute(d, images, a.layout)
    except ValueError as e:
        raise SystemExit(str(e))
    keys, res = r.keys, r.residual_mm

    print(f"\n사진 {images}")
    print(f"대응 코너 {len(keys)}개 (마커 {sorted({i for i, _ in keys})}) · ODM→W 축척 {r.s:.5f}")
    print(f"잔차 중앙 {np.median(res):.1f} mm · 최대 {res.max():.1f} mm\n")
    for i in sorted({i for i, _ in keys}):
        rr = [x for (m, _), x in zip(keys, res) if m == i]
        n = [r.used[k] for k in keys if k[0] == i]
        print(f"  ID {i}: 코너 잔차 {' '.join(f'{x:.1f}' for x in rr)} mm   관측 {min(n)}~{max(n)}장")

    (d / "to_W.json").write_text(json.dumps(r.to_json(a.layout.name), indent=2, ensure_ascii=False))
    print(f"\n저장: {d / 'to_W.json'}")


if __name__ == "__main__":
    main()
