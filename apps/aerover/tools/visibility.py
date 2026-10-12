"""갠트리 한 바퀴 동안 마커가 몇 개 보이나 — 3D 맵(건물)의 가림까지 넣은 시뮬레이션.

    uv run python -m tools.visibility 3D_model/<회차>
    uv run python -m tools.visibility 3D_model/<회차> --suggest      # 마커를 하나 더 붙일 자리 추천
    uv run python -m tools.visibility 3D_model/<회차> --suggest --need 3   # 3개 미만 구간 기준 (2개는 자세가 불안하다)

갠트리가 없어도 "어느 각도에서 자세가 끊기나" 를 미리 본다. 배치표(markers_W.json)나
맵이 바뀌면 다시 돌린다.

가상 카메라: 판 중심 위 원궤도 (반지름 1.055m, 판 윗면 위 1.114m), 판 중심 조준,
aruco/camera_1280x960.json 의 K · 왜곡. 마커가 "보인다" 는 넷 다 만족할 때:

    1. 코너 4개가 화면 안 (가장자리 여백 MARGIN_PX)
    2. 화면에서 한 변 >= MIN_SIDE_PX   — 6cm 실물 손 촬영에서 잡힌 가장 작은 것이 26px
    3. 마커 법선과 시선의 각 <= MAX_TILT_DEG
    4. 카메라 → 코너 광선이 메시에 먼저 맞지 않는다 (건물 · 소품에 가리지 않음)

실물 검출은 흐림 · 조명에 따라 이보다 나쁘다 — 이 수치는 **상한**이다.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from src.core import wframe

RADIUS, HEIGHT = 1.055, 1.114       # README 시연 환경
MARGIN_PX = 4
MIN_SIDE_PX = 25
MAX_TILT_DEG = 70
# 코너보다 이만큼 앞에서 맞아야 가림. 메시 판 표면이 마커 코너보다 몇 mm 높아(요철 + 정합 잔차)
# 비스듬한 광선이 코너 5~10mm 앞 판에 먼저 닿는다 — 5mm 로 두면 판 위 마커가 전부 가린 것으로 나왔다.
# 진짜 가림(건물)은 수십 cm 앞에서 맞는다. 대신 마커 바로 옆 3cm 안의 작은 소품은 못 잡는다.
OCCLUDE_TOL = 0.03
W_IMG, H_IMG = wframe.FRAME_SIZE


def look_at(C: np.ndarray, target: np.ndarray) -> np.ndarray:
    """W → 카메라 회전 R (행 = 카메라 x, y, z 축). OpenCV 규약: z 앞, y 아래."""
    z = target - C
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    return np.vstack([x, np.cross(z, x), z])


def visible(C, R, corners: np.ndarray, K, dist, mesh: wframe.Mesh | None) -> tuple[bool, str]:
    """(보이나, 안 보이는 이유)."""
    rvec = cv2.Rodrigues(R)[0]
    uv = cv2.projectPoints(corners, rvec, -R @ C, K, dist)[0].reshape(4, 2)
    if (R @ (corners - C).T)[2].min() <= 0:
        return False, "뒤"
    if (uv < MARGIN_PX).any() or (uv[:, 0] > W_IMG - MARGIN_PX).any() or (uv[:, 1] > H_IMG - MARGIN_PX).any():
        return False, "화면 밖"
    if np.linalg.norm(uv - np.roll(uv, 1, 0), axis=1).min() < MIN_SIDE_PX:
        return False, "작음"
    n = np.cross(corners[1] - corners[0], corners[3] - corners[0])
    view = corners.mean(0) - C
    if np.degrees(np.arccos(abs(n @ view) / np.linalg.norm(n) / np.linalg.norm(view))) > MAX_TILT_DEG:
        return False, "기울기"
    if mesh is not None:
        d = corners - C
        L = np.linalg.norm(d, axis=1)
        if (mesh.intersect(C, d / L[:, None]) < L - OCCLUDE_TOL).any():
            return False, "가림"
    return True, ""


def main() -> None:
    ap = argparse.ArgumentParser(description="갠트리 궤도 마커 가시성 (건물 가림 포함)")
    ap.add_argument("model_dir", type=Path, help="3D_model/<회차> — to_W.json 이 있는 곳")
    ap.add_argument("--step", type=float, default=5.0, help="궤도 각도 간격 (도)")
    ap.add_argument("--suggest", action="store_true", help="약한 구간을 메울 추가 마커 자리")
    ap.add_argument("--need", type=int, default=2, help="이만큼 안 보이면 약한 구간 (추천 기준)")
    a = ap.parse_args()

    est = wframe.load_estimator()
    K, dist, layout = est.K, est.dist, est.layout
    glb = wframe.map_file(a.model_dir)
    mesh = wframe.load_mesh_w(glb, wframe.load_to_w(a.model_dir))
    # 판 중심 = 배치 범위의 가운데. 평균을 쓰면 한쪽에 몰린 보강 마커(8 · 9)가 중심을 끌고 간다 (09-29: 11cm)
    pts = np.vstack(list(layout.values()))
    center = (pts.min(0) + pts.max(0)) / 2
    center[2] = 0.0

    angles = np.arange(0, 360, a.step)
    poses = []
    for ang in angles:
        t = np.radians(ang)
        C = center + [RADIUS * np.cos(t), RADIUS * np.sin(t), HEIGHT]
        poses.append((C, look_at(C, center)))

    print(f"{a.model_dir.name}/{glb.name} · 궤도 {len(angles)}곳 ({a.step:g}° 간격) · 마커 {sorted(layout)}")
    print(f"각도 0° = 판 중심에서 +X 쪽, 90° = +Y 쪽 (W)\n")
    counts, counts_open, why = [], [], {}
    for ang, (C, R) in zip(angles, poses):
        seen, blocked = [], []
        for i, c in sorted(layout.items()):
            ok, reason = visible(C, R, c, K, dist, mesh)
            if ok:
                seen.append(i)
            elif reason == "가림":
                blocked.append(i)
            why[reason] = why.get(reason, 0) + (not ok)
        counts.append(len(seen))
        counts_open.append(len(seen) + len(blocked))
        flag = "  ← 2개 미만" if len(seen) < 2 else ""
        print(f"  {ang:5.0f}°  보임 {len(seen)} {seen}" + (f"  · 가림 {blocked}" if blocked else "") + flag)

    c, o = np.array(counts), np.array(counts_open)
    print(f"\n요약 — 건물 있음: 최소 {c.min()} · 평균 {c.mean():.1f} · 2개 미만 {np.mean(c < 2) * 100:.0f}%"
          f"   |   건물 없다면: 최소 {o.min()} · 평균 {o.mean():.1f} · 2개 미만 {np.mean(o < 2) * 100:.0f}%")
    print("안 보인 이유 (마커 × 위치): " + " · ".join(f"{k} {v}" for k, v in why.items() if k))

    if a.suggest and (c < a.need).any():
        suggest(angles, poses, c, K, dist, mesh, layout, a.need)


def suggest(angles, poses, counts, K, dist, mesh, layout, need: int) -> None:
    """판 가장자리 둘레(안쪽 7cm)에 5cm 간격 후보를 놓고, `need` 개 미만 위치를 몇 곳 메우는지 센다."""
    size = np.linalg.norm(next(iter(layout.values()))[1] - next(iter(layout.values()))[0])
    h = size / 2
    lo, hi = 0.07, 1.13
    cand = [(x, y) for x in np.arange(lo, hi + 1e-9, 0.05) for y in (lo, hi)]
    cand += [(x, y) for y in np.arange(lo + 0.05, hi - 0.05 + 1e-9, 0.05) for x in (lo, hi)]
    taken = np.array([c.mean(0)[:2] for c in layout.values()])
    weak = [k for k, n in enumerate(counts) if n < need]
    scored = []
    for x, y in cand:
        if np.linalg.norm(taken - [x, y], axis=1).min() < 0.15:
            continue                                    # 기존 마커와 겹친다
        # 판 윗면 높이 = 그 자리 메시 높이 (위에서 아래로 쏴서)
        t = mesh.intersect(np.array([x, y, 1.0]), np.array([[0, 0, -1.0]]))[0]
        z = 1.0 - t if np.isfinite(t) else 0.0
        if z > 0.02:
            continue                                    # 소품 · 건물 위 — 붙일 수 없다
        corners = np.array([[x - h, y + h, z], [x + h, y + h, z], [x + h, y - h, z], [x - h, y - h, z]])
        fills = [k for k in weak if counts[k] + visible(*poses[k], corners, K, dist, mesh)[0] >= need]
        scored.append((len(fills), x, y, fills))
    scored.sort(key=lambda s: -s[0])
    print(f"\n추가 마커 자리 추천 ({need}개 미만 {len(weak)}곳 {[f'{angles[k]:.0f}°' for k in weak]} 중 메우는 수, "
          f"판 윗면 · 기존 마커에서 15cm 이상)")
    for n, x, y, fills in scored[:5]:
        print(f"  W ({x:.2f}, {y:.2f})  → {n}곳 메움  {[f'{angles[k]:.0f}°' for k in fills]}")


if __name__ == "__main__":
    main()
