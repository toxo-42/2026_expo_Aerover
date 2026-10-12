"""3차 정합의 현실 정확도 — 무엇이 오차를 지배하나.

합성 마커를 실제 1차-B 촬영 기하(112장 포즈)에 놓고 조건을 바꿔가며 돌린다.
결론은 ArUco_준비작업.md 에 반영한다.

    uv run python sensitivity.py
"""
from __future__ import annotations

import numpy as np
import cv2

import align

Z_FLOOR = -2.31
SCALE = 14.87 / 1.20                 # ODM 단위/m — 매트 실측 1.2m 기준
MAT = 14.87                          # 매트 한 변 (ODM 단위)
NORMAL = np.array([0, 0, 1.0])       # 마커는 바닥에 평평 (바닥 기울기 6.1° 는 무시)

# 마커 자리 — 매트 네 귀퉁이 + 가운데 높은 것 + 여분
SPOTS = [(-12.0, -13.5, 0.0), (0.8, -13.5, 0.0), (-12.0, -0.6, 0.0),
         (0.8, -0.6, 0.0), (-5.6, -7.0, 1.24), (-5.6, -13.5, 0.0)]

S_TRUE = 1.0 / SCALE
R_TRUE, _ = cv2.Rodrigues(np.array([0.04, -0.02, 0.9]))
T_TRUE = np.array([0.75, 0.62, 0.013])


def observations(cam, shots, X, max_grazing_deg=75.0):
    """점 X 를 볼 수 있는 (R, C) 목록. 뒷면·화면밖·너무 비스듬한 각을 뺀다."""
    out = []
    for R, C in shots.values():
        v = C - X
        d = np.linalg.norm(v)
        cosang = float(v @ NORMAL) / d
        if cosang <= np.cos(np.radians(max_grazing_deg)):
            continue
        uv, z = cam.project(X, R, C)
        if z[0] <= 0 or not (0 <= uv[0, 0] < cam.width and 0 <= uv[0, 1] < cam.height):
            continue
        out.append((R, C, uv[0]))
    return out


def run(cam, shots, n_markers, n_views, px_noise, meas_mm, seed=0):
    """한 조건을 돌려 (축척오차%, 회전오차도, 매트 위 점의 RMS 위치오차 mm)."""
    rng = np.random.default_rng(seed)
    size_odm = 0.10 * SCALE
    src, dst = [], []
    for mid, (x, y, dz) in enumerate(SPOTS[:n_markers]):
        centre = np.array([x, y, Z_FLOOR + dz])
        # 실측 오차는 마커당 하나 — 중심 하나를 재서 코너 4개를 계산하므로 공유된다
        derr = rng.normal(0, meas_mm / 1000.0, 3)
        for X in align.world_corners(centre, size_odm, "+Y"):
            obs = observations(cam, shots, X)
            if len(obs) < 3:
                continue
            if n_views is not None and len(obs) > n_views:
                obs = [obs[i] for i in rng.choice(len(obs), n_views, replace=False)]
            src.append(align.triangulate(
                [(R, C, cam.undistort(uv + rng.normal(0, px_noise, 2))[0]) for R, C, uv in obs]))
            dst.append(align.apply(S_TRUE, R_TRUE, T_TRUE, X)[0] + derr)
    src, dst = np.array(src), np.array(dst)
    if len(src) < 3:
        return None
    s, R, t = align.umeyama(src, dst)

    # 매트 전면에 격자를 깔아 실제 위치 오차를 잰다 — 이게 사용자가 체감하는 값이다
    gx, gy = np.meshgrid(np.linspace(-12.5, 1.3, 7), np.linspace(-14.0, -0.1, 7))
    G = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, Z_FLOOR)], 1)
    err = np.linalg.norm(align.apply(s, R, t, G) - align.apply(S_TRUE, R_TRUE, T_TRUE, G), axis=1)
    ang = np.degrees(np.linalg.norm(cv2.Rodrigues(R.T @ R_TRUE)[0]))
    return abs(s - S_TRUE) / S_TRUE * 100, ang, np.sqrt((err ** 2).mean()) * 1000


def table(cam, shots, title, rows, base):
    print(f"\n{title}")
    print(f"  {'조건':<22}{'축척오차':>9}{'회전오차':>10}{'매트 위 위치오차 RMS':>22}")
    for label, kw in rows:
        v = [run(cam, shots, seed=k, **{**base, **kw}) for k in range(12)]
        v = np.array([x for x in v if x])
        print(f"  {label:<22}{v[:,0].mean():>8.3f}%{v[:,1].mean():>9.3f}도"
              f"{v[:,2].mean():>18.1f} mm")


def main() -> None:
    cam = align.Camera.load("data/cameras.json")
    shots = align.load_shots("data/shots.geojson")

    X = np.array([SPOTS[0][0], SPOTS[0][1], Z_FLOOR])
    print(f"기하 확인 — 매트 귀퉁이 마커를 볼 수 있는 장수: {len(observations(cam, shots, X))}/112")

    base = dict(n_markers=4, n_views=None, px_noise=1.0, meas_mm=2.0)

    table(cam, shots, "1. 실측 정밀도 (다른 조건 고정: 마커4, 전체시점, 픽셀잡음 1px)",
          [(f"자 오차 {m}mm", dict(meas_mm=m)) for m in (0, 1, 2, 5, 10)], base)

    table(cam, shots, "2. ArUco 코너 픽셀 잡음 (자 오차 2mm)",
          [(f"코너잡음 {p}px", dict(px_noise=p)) for p in (0.5, 1, 3, 8)], base)

    table(cam, shots, "3. 코너당 관측 장수 (자 오차 2mm, 코너잡음 1px)",
          [(f"{n if n else '전체'}장", dict(n_views=n)) for n in (3, 5, 10, 30, None)], base)

    table(cam, shots, "4. 마커 개수 (자 오차 2mm, 코너잡음 1px)",
          [(f"마커 {n}개", dict(n_markers=n)) for n in (3, 4, 5, 6)], base)

    table(cam, shots, "5. 실측을 못 할 때 — 자 오차만 큰 경우",
          [("자 5mm · 관측 5장", dict(meas_mm=5, n_views=5)),
           ("자 2mm · 관측 5장", dict(meas_mm=2, n_views=5)),
           ("자 2mm · 마커 6개", dict(meas_mm=2, n_markers=6)),
           ("자 1mm · 마커 6개", dict(meas_mm=1, n_markers=6))], base)


if __name__ == "__main__":
    main()
