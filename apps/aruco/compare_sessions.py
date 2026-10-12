"""여러 촬영 회차의 마커 검출 · 자세를 나란히 비교하고, 장마다 검출 결과를 그려 저장한다.

    uv run python compare_sessions.py ../aerover/sessions/20260929_194931 --layout markers_W.json \
        ../aerover/sessions/20260929_212559 --layout <5cm 배치표>.json --draw out/

회차마다 --layout 을 하나씩 붙인다 (마커 크기가 다르면 배치표도 다르다).
--draw 를 주면 out/<회차>/<사진>.jpg 에 마커 테두리 · ID · 화면 크기(px) · 자세 결과를 그린다.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

import pose as P

GOOD, BAD, TEXT = (80, 200, 80), (60, 60, 230), (255, 255, 255)


@dataclass
class FrameResult:
    name: str
    sizes_px: dict[int, float]          # {ID: 화면상 한 변 평균 px}
    reproj_px: float | None             # 자세를 못 풀면 None


def side_px(c: np.ndarray) -> float:
    return float(np.linalg.norm(np.diff(np.r_[c, c[:1]], axis=0), axis=1).mean())


def analyze(img: np.ndarray, est: P.PoseEstimator) -> tuple[dict[int, np.ndarray], P.Pose | None]:
    """한 장 → (검출 {ID: 코너}, 자세). 마커 2개 미만이면 자세를 풀지 않는다 (1개는 각도가 튄다)."""
    seen = est.detect(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    return seen, (est.estimate(seen) if len(seen) >= 2 else None)


def draw(img: np.ndarray, seen: dict[int, np.ndarray], pose: P.Pose | None) -> np.ndarray:
    out = img.copy()
    for i, c in seen.items():
        cv2.polylines(out, [c.astype(np.int32)], True, GOOD, 2)
        x, y = c.min(0).astype(int)
        cv2.putText(out, f"{i} {side_px(c):.0f}px", (x, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, GOOD, 2)
    msg = f"markers {len(seen)}  " + (f"reproj {pose.err_px:.2f}px" if pose else "POSE FAIL")
    cv2.rectangle(out, (0, 0), (360, 34), (0, 0, 0), -1)
    cv2.putText(out, msg, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, TEXT if pose else BAD, 2)
    return out


def run_session(folder: Path, layout: Path, draw_dir: Path | None) -> list[FrameResult]:
    est = P.PoseEstimator(*P.load_camera(), P.load_layout(layout))
    results = []
    for f in sorted(folder.glob("*.jpg")):
        img = cv2.imread(str(f))
        seen, pose = analyze(img, est)
        results.append(FrameResult(f.name, {i: side_px(c) for i, c in seen.items()}, pose.err_px if pose else None))
        if draw_dir:
            (draw_dir / folder.name).mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(draw_dir / folder.name / f.name), draw(img, seen, pose))
    return results


def summarize(name: str, rs: list[FrameResult]) -> str:
    counts = np.array([len(r.sizes_px) for r in rs])
    sizes = np.array([s for r in rs for s in r.sizes_px.values()] or [np.nan])
    errs = np.array([r.reproj_px for r in rs if r.reproj_px is not None] or [np.nan])
    return (f"{name:18s} {len(rs):4d}장 | 장당 마커 평균 {counts.mean():.2f} · 2개 미만 {np.mean(counts < 2) * 100:4.0f}%"
            f" | 마커 크기 중앙 {np.nanmedian(sizes):4.0f}px (최소 {np.nanmin(sizes):.0f})"
            f" | 자세 실패 {sum(r.reproj_px is None for r in rs):3d}장 · 재투영 중앙 {np.nanmedian(errs):.2f}px")


def main() -> None:
    ap = argparse.ArgumentParser(description="촬영 회차별 마커 검출 · 자세 비교")
    ap.add_argument("folders", type=Path, nargs="+")
    ap.add_argument("--layout", type=Path, action="append", required=True, help="회차마다 하나씩, 같은 순서로")
    ap.add_argument("--draw", type=Path, help="검출 결과를 그린 사진을 저장할 폴더")
    a = ap.parse_intermixed_args()          # 회차 · --layout 을 번갈아 쓸 수 있게
    if len(a.layout) != len(a.folders):
        raise SystemExit(f"회차 {len(a.folders)}개에 --layout {len(a.layout)}개 — 개수를 맞춘다")

    for folder, layout in zip(a.folders, a.layout):
        print(summarize(folder.name, run_session(folder, layout, a.draw)))
    if a.draw:
        print(f"\n그린 사진: {a.draw}/<회차>/")


if __name__ == "__main__":
    main()
