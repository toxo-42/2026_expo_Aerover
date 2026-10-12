"""마커 크기 판정 — 한 바퀴 촬영본 하나로 "이 크기로 시연해도 되나" 를 한 번에 본다.

    uv run python size_check.py ../aerover/sessions/<회차>
    uv run python size_check.py ../aerover/sessions/<회차> --model ../aerover/3D_model/<맵회차>

크기마다 (10 → 8 → 6 → 5cm) 같은 조건으로 찍어 이 출력을 나란히 비교한다.
조건이 다르면 크기 차이인지 촬영 차이인지 가릴 수 없다 — 그래서 **촬영 조건부터 출력한다.**

판정 네 가지:

  1. 촬영 조건   카메라 높이 · 판 중심까지 거리 · 흐림. 갠트리 사양(1.114m · 1.53m)과 얼마나 다른가
  2. 자세        프레임당 마커 수 · 2개 미만 비율 · 재투영.  ← 크기 한계는 여기서 먼저 드러난다
  3. 대상 좌표   마커 하나를 빼고 나머지로 자세를 풀어, 뺀 마커 중심을 역투영 → 배치표와의 수평 거리.
                 요구조자 역투영과 같은 계산이다 (정답을 아는 점으로 바꿨을 뿐).
                 메시 대신 그 마커의 평면과 교차 — 3D 맵이 지금 판과 달라도 영향이 없다
  4. 배치 · 정합  배치표(markers_W.json)의 마커별 관측 장수, --model 이 있으면 to_W.json 잔차

3번 결과가 마커마다 **한쪽으로 일정하게** 밀려 있으면 (치우침 ≫ 흩어짐) 배치표가 실물과 다르다
(마커가 밀렸다) → marker_map.py 를 다시 돌린다. 흩어짐만 크면 촬영(흐림 · 거리)이나 크기 문제다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

import pose as P
from backproject import pixel_rays

GANTRY_H, GANTRY_D = 1.114, 1.53        # 판 윗면 위 높이 · 판 중심까지 거리 (README 시연 환경)
MIN_OTHERS = 2                          # 뺀 마커 말고 이만큼은 보여야 자세를 믿는다


def sharpness(gray: np.ndarray, corners: np.ndarray) -> float:
    """마커 둘레의 라플라시안 분산 — 클수록 선명. 같은 크기끼리만 비교한다."""
    x0, y0 = np.maximum(corners.min(0).astype(int) - 4, 0)
    x1, y1 = corners.max(0).astype(int) + 4
    return float(cv2.Laplacian(gray[y0:y1, x0:x1], cv2.CV_64F).var())


def main() -> None:
    ap = argparse.ArgumentParser(description="마커 크기 판정 (촬영 조건 · 자세 · 대상 좌표 · 배치)")
    ap.add_argument("folder", type=Path, help="한 바퀴 촬영본 폴더 (*.jpg)")
    ap.add_argument("--layout", type=Path, default=P.LAYOUT_FILE)
    ap.add_argument("--model", type=Path, help="3D 맵 회차 폴더 — to_W.json 잔차를 같이 본다")
    a = ap.parse_args()

    K, dist = P.load_camera()
    layout = P.load_layout(a.layout)
    est = P.PoseEstimator(K, dist, layout)
    center = np.vstack(list(layout.values())).mean(0)

    counts, errs, heights, dists, sharp, sides = [], [], [], [], [], []
    loo: dict[int, list[np.ndarray]] = {i: [] for i in layout}
    files = sorted(a.folder.glob("*.jpg"))
    for f in files:
        gray = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
        seen = est.detect(gray)
        counts.append(len(seen))
        for c in seen.values():
            sharp.append(sharpness(gray, c))
            sides.append(np.linalg.norm(c - np.roll(c, 1, 0), axis=1).mean())
        pose = est.estimate(seen) if len(seen) >= 2 else None
        if pose is None:
            continue
        errs.append(pose.err_px)
        heights.append(pose.C[2] - center[2])
        dists.append(np.linalg.norm(pose.C - center))

        # 3. 한 개씩 빼고 — 뺀 마커 중심 픽셀을 그 마커 평면(배치표)과 교차
        for k in seen:
            others = {i: c for i, c in seen.items() if i != k}
            if len(others) < MIN_OTHERS:
                continue
            p = est.estimate(others)
            if p is None:
                continue
            d = pixel_rays(seen[k].mean(0, keepdims=True), p, K, dist)[0]
            corners = layout[k]
            n = np.cross(corners[1] - corners[0], corners[3] - corners[0])
            t = n @ (corners.mean(0) - p.C) / (n @ d)
            loo[k].append((p.C + t * d - corners.mean(0))[:2] * 1000)

    c = np.array(counts)
    print(f"{a.folder.name} — {len(files)}장, 배치표 {a.layout.name} (한 변 {json.loads(a.layout.read_text())['size_m'] * 100:.0f}cm)\n")

    print("1. 촬영 조건")
    if heights:
        print(f"   카메라 높이 중앙 {np.median(heights):.2f} m (갠트리 {GANTRY_H}) · "
              f"판 중심 거리 중앙 {np.median(dists):.2f} m (갠트리 {GANTRY_D})")
    if sides:
        print(f"   마커 화면 크기 중앙 {np.median(sides):.0f} px (최소 {min(sides):.0f}) · "
              f"선명도 중앙 {np.median(sharp):.0f} (같은 크기끼리만 비교)")

    print("2. 자세")
    print(f"   프레임당 마커 최소 {c.min()} · 평균 {c.mean():.1f} · 2개 미만 {np.mean(c < 2) * 100:.0f}% · "
          f"자세 실패 {np.sum(c < 2)}장" + (f" · 재투영 중앙 {np.median(errs):.2f} px" if errs else ""))

    print("3. 대상 좌표 (마커 하나 빼고 역투영, 수평 오차 mm)")
    allv = [v for vs in loo.values() for v in vs]
    if allv:
        r = np.linalg.norm(allv, axis=1)
        print(f"   전체 {len(r)}건 — 중앙 {np.median(r):.1f} · 95% {np.percentile(r, 95):.1f} · 최대 {r.max():.1f}")
        print("   마커   건수   치우침(x, y)     흩어짐   ← 치우침 ≫ 흩어짐 이면 배치표가 실물과 다르다")
        for k, vs in loo.items():
            if len(vs) >= 2:
                v = np.array(vs)
                bias, spread = v.mean(0), np.linalg.norm(v - v.mean(0), axis=1).mean()
                flag = "  ← 밀림?" if np.linalg.norm(bias) > max(2 * spread, 5) else ""
                print(f"   {k:>4} {len(v):>6}   ({bias[0]:+6.1f}, {bias[1]:+6.1f})  {spread:6.1f}{flag}")
    else:
        print("   계산할 수 없음 — 마커가 3개 이상 보인 장이 없다")

    print("4. 배치 · 정합")
    seen_n = {k: m.get("seen") for k, m in json.loads(a.layout.read_text())["markers"].items()}
    print(f"   배치표 마커별 관측 장수 {seen_n}  (10장 미만이면 그 마커 좌표를 덜 믿는다)")
    if a.model:
        tw = json.loads((a.model / "to_W.json").read_text())
        print(f"   {a.model.name}/to_W.json — 코너 {tw['corners']}개, "
              f"잔차 중앙 {tw['residual_median_mm']} / 최대 {tw['residual_max_mm']} mm")


if __name__ == "__main__":
    main()
