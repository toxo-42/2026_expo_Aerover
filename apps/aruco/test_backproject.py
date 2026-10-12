"""backproject.py 검증 — 판 + 상자 건물 합성 메시로.

    1. 정답 자세: 메시 위 점을 투영했다 되돌리면 그 점이 나오는가 (바닥 · 벽 · 옥상)
    2. 가림: 건물 뒤에 숨은 바닥 점은 건물 벽이 나와야 한다 (가장 가까운 면)
    3. 빗나감: 메시 밖(하늘)을 보는 광선은 NaN
    4. 끝까지: test_pose 의 렌더링 → pose.py 로 푼 자세 → backproject (실제 흐름 그대로)

    uv run python test_backproject.py
"""
from __future__ import annotations

import time

import cv2
import numpy as np

import pose as P
import test_pose as T
from backproject import Mesh, backproject

PASS, FAIL = "\033[32m통과\033[0m", "\033[31m실패\033[0m"
_results: list[bool] = []

# 건물: 판 위 (0.25~0.55, 0.75~1.05), 높이 0.45 — 실물 왼쪽 건물 자리쯤
BX0, BX1, BY0, BY1, BH = 0.25, 0.55, 0.75, 1.05, 0.45


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def box_mesh() -> tuple[np.ndarray, np.ndarray]:
    """판(1.2m 정사각, Z=0) + 상자 건물. 삼각형 14개."""
    v = [[0, 0, 0], [1.2, 0, 0], [1.2, 1.2, 0], [0, 1.2, 0]]
    f = [[0, 1, 2], [0, 2, 3]]
    b = [[x, y, z] for z in (0, BH) for (x, y) in ((BX0, BY0), (BX1, BY0), (BX1, BY1), (BX0, BY1))]
    o = len(v); v += b
    quads = [(4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]   # 옥상 + 벽 4
    for a, bb, c, d in quads:
        f += [[o + a, o + bb, o + c], [o + a, o + c, o + d]]
    return np.array(v, float), np.array(f)


def truth_pose(th: float) -> P.Pose:
    C = T.CENTER + [T.ORBIT_R * np.cos(th), T.ORBIT_R * np.sin(th), T.ORBIT_Z]
    return P.Pose(T.look_at(C, T.CENTER), C, [], 0.0)


def project(X: np.ndarray, pose: P.Pose) -> np.ndarray:
    rvec, tvec = pose.rvec_tvec
    return cv2.projectPoints(X, rvec, tvec, T.K, T.D)[0].reshape(-1, 2)


def main() -> None:
    mesh = Mesh(*box_mesh())
    K, D = T.K, T.D

    print("1. 정답 자세 — 투영했다 되돌리기")
    front = truth_pose(-np.pi / 2)                      # 판 앞쪽(-Y)에서 뒤쪽 건물을 본다
    pts = np.array([[0.60, 0.40, 0.0],                   # 바닥
                    [0.40, BY0, 0.20],                   # 건물 앞벽 (2층쯤)
                    [0.40, 0.90, BH],                    # 옥상
                    [1.00, 0.30, 0.0]])                  # 바닥 (화면 가장자리 쪽)
    got = backproject(project(pts, front), front, K, D, mesh)
    e = np.linalg.norm(got - pts, axis=1) * 1000
    check("바닥 · 벽 · 옥상 점 복원 < 0.1 mm", e.max() < 0.1, " / ".join(f"{x:.4f}" for x in e) + " mm")

    print("\n2. 가림 — 건물 뒤 바닥 점")
    hidden = np.array([[0.40, 1.10, 0.0]])              # 건물 바로 뒤 (앞에서 보면 가려진다)
    got = backproject(project(hidden, front), front, K, D, mesh)[0]
    on_wall = abs(got[1] - BY0) < 1e-6 and BX0 <= got[0] <= BX1 and 0 <= got[2] <= BH
    check("가장 가까운 면(건물 앞벽)이 나온다", on_wall, f"({got[0]:.3f}, {got[1]:.3f}, {got[2]:.3f})")

    print("\n3. 빗나감")
    sky = np.array([[640.0, 5.0]])                       # 화면 맨 위 — 판 너머
    rays_hit = backproject(sky, front, K, D, mesh)
    check("메시 밖 광선은 NaN", bool(np.isnan(rays_hit).all()))

    print("\n4. 끝까지 — 렌더링 → pose.py → backproject (궤도 12곳)")
    est = P.PoseEstimator(K, D, T.LAYOUT)
    rng = np.random.default_rng(1)
    targets = np.array([[x, y, 0.0] for x in (0.2, 0.6, 1.0) for y in (0.2, 0.5)] +
                       [[0.40, BY0, 0.30], [0.40, 0.90, BH], [BX1, 0.90, 0.15]])   # 벽 · 옥상 · 옆벽
    errs, t_ms = [], []
    for th in np.linspace(0, 2 * np.pi, 12, endpoint=False):
        tp = truth_pose(th)
        p = est(T.render(tp.R, tp.C, rng))
        uv = project(targets, tp)
        # 정답 자세로 봤을 때 가려지지 않고 화면 안인 점만 (가려진 점은 2절에서 따로 봤다)
        first = backproject(uv, tp, K, D, mesh)
        vis = ((uv[:, 0] > 0) & (uv[:, 0] < 1280) & (uv[:, 1] > 0) & (uv[:, 1] < 960)
               & (np.linalg.norm(first - targets, axis=1) < 1e-6))
        t0 = time.perf_counter()
        got = backproject(uv[vis], p, K, D, mesh)
        t_ms.append((time.perf_counter() - t0) * 1000)
        errs += list(np.linalg.norm(got - targets[vis], axis=1) * 1000)
    errs = np.array(errs)
    check("대상 좌표 오차 < 5 mm (마커 → 자세 → 메시 교차)", np.nanmax(errs) < 5,
          f"{len(errs)}점, 중앙 {np.nanmedian(errs):.2f} / 최대 {np.nanmax(errs):.2f} mm")

    print("\n5. 속도 (실제 3D 맵 크기)")
    rng = np.random.default_rng(2)
    big = Mesh(rng.uniform(0, 1.2, (120_000, 3)), rng.integers(0, 120_000, (230_000, 3)))
    uv = rng.uniform([0, 0], [1280, 960], (5, 2))
    t0 = time.perf_counter()
    backproject(uv, front, K, D, big)
    ms = (time.perf_counter() - t0) * 1000 / 5
    check("삼각형 23만 개에서 광선 하나 < 100 ms", ms < 100, f"{ms:.0f} ms / 광선")

    n = sum(_results)
    print("\n" + "=" * 60)
    print(f"{n}/{len(_results)} 통과" + ("" if n == len(_results) else f"  — {len(_results) - n}건 실패"))


if __name__ == "__main__":
    main()
