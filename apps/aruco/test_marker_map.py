"""marker_map.py 검증 — 정답 배치를 아는 합성 화면으로.

1.2m 판 둘레에 마커 8개를 **아무 방향 · 조금씩 비껴** 붙여 두고(자로 재지 않은 실물처럼),
갠트리 궤도 30곳에서 렌더링한다. marker_map 이 사진만으로 배치를 되찾는지 본다.

    1. 모양: 복원한 코너와 정답 코너를 강체 정합(회전 · 이동만, 축척 없음) 했을 때의 잔차
    2. W 규칙: 가장 작은 ID 가 앞-왼쪽, 축이 판 변과 나란한가
    3. 축척: 마커 한 변을 1% 틀리게 주면 배치도 1% 틀어지는가 (축척이 한 변에서만 온다는 확인)

    uv run python test_marker_map.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import numpy as np

import marker_map as MM
import test_pose as T
from align import ARUCO_DICT

PASS, FAIL = "\033[32m통과\033[0m", "\033[31m실패\033[0m"
_results: list[bool] = []
SIZE, Z = 0.10, 0.005


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def truth_layout(rng: np.random.Generator) -> dict[int, np.ndarray]:
    """문서 표 자리에서 ±2cm 비껴, 윗변은 네 방향 중 아무거나 + ±10° 비틀어 붙인다."""
    spots = [(0.07, 0.07), (0.60, 0.07), (1.13, 0.07), (1.13, 0.60),
             (1.13, 1.13), (0.60, 1.13), (0.07, 1.13), (0.07, 0.60)]
    out = {}
    for i, (x, y) in enumerate(spots):
        c = np.array([x, y]) + rng.uniform(-0.02, 0.02, 2)
        if i == 0:
            c = np.array([0.07, 0.07])                   # 앞-왼 기준 마커는 제자리 (W 규칙 확인용)
        a = rng.integers(4) * np.pi / 2 + np.radians(rng.uniform(-10, 10))
        R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        loc = MM.local_corners(SIZE)[:, :2] @ R.T + c
        out[i] = np.c_[loc, np.full(4, Z)]
    return out


def render(R, C, lay, rng) -> np.ndarray:
    """test_pose.render 와 같되 마커가 아무 방향이어도 된다 (마커 좌표계로 되돌려 칠한다)."""
    d = T.RAYS @ R
    s = (Z - C[2]) / d[:, 2]
    X, Y = C[0] + s * d[:, 0], C[1] + s * d[:, 1]
    img = np.full(len(X), 170.0)
    on = (X >= 0) & (X <= 1.2) & (Y >= 0) & (Y <= 1.2)
    img[on] = 95 + 25 * np.sin(X[on] * 180) * np.sin(Y[on] * 150)
    bits = {i: cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(ARUCO_DICT), i, 600) for i in lay}
    for i, c4 in lay.items():
        o = c4[0, :2]                                    # 좌상 코너
        ex = (c4[1, :2] - o) / SIZE                      # 마커 오른쪽
        ey = (c4[3, :2] - o) / SIZE                      # 마커 아래쪽
        u = (X - o[0]) * ex[0] + (Y - o[1]) * ex[1]
        v = (X - o[0]) * ey[0] + (Y - o[1]) * ey[1]
        m = (u > -0.02) & (u < SIZE + 0.02) & (v > -0.02) & (v < SIZE + 0.02)
        img[m] = 235.0
        k = m & (u >= 0) & (u < SIZE) & (v >= 0) & (v < SIZE)
        img[k] = np.where(bits[i][(v[k] / SIZE * 600).astype(int), (u[k] / SIZE * 600).astype(int)] > 127, 235.0, 25.0)
    img[s <= 0] = 60.0
    img = cv2.GaussianBlur(img.reshape(T.H, T.W), (0, 0), 1.2) + rng.normal(0, 2.0, (T.H, T.W))
    return np.clip(img, 0, 255).astype(np.uint8)


def kabsch(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """A 를 B 에 강체 정합한 뒤의 A (축척 없음)."""
    a, b = A.mean(0), B.mean(0)
    U, _, Vt = np.linalg.svd((A - a).T @ (B - b))
    Dm = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    return (Vt.T @ Dm @ U.T @ (A - a).T).T + b


def run_map(folder: Path, size: float):
    names, frames = MM.detect_folder(folder)
    ref, marker, cams = MM.initial_guess(frames, T.K, T.D, size)
    corners, centers, err = MM.bundle_adjust(frames, T.K, T.D, size, ref, marker, cams)
    return MM.to_world(corners, centers, 0.07), err, len(frames)


def main() -> None:
    rng = np.random.default_rng(3)
    lay = truth_layout(rng)
    with tempfile.TemporaryDirectory() as tmp:
        for k, th in enumerate(np.linspace(0, 2 * np.pi, 30, endpoint=False)):
            C = T.CENTER + [T.ORBIT_R * np.cos(th), T.ORBIT_R * np.sin(th), T.ORBIT_Z + rng.uniform(-0.05, 0.05)]
            cv2.imwrite(str(Path(tmp) / f"{k:03d}.jpg"), render(T.look_at(C, T.CENTER), C, lay, rng),
                        [cv2.IMWRITE_JPEG_QUALITY, 95])
        print("궤도 30곳 렌더링 → marker_map …")
        W, err, n = run_map(Path(tmp), SIZE)
        W99, _, _ = run_map(Path(tmp), SIZE * 0.99)

    ids = sorted(lay)
    A = np.vstack([W[i] for i in ids]); B = np.vstack([lay[i] for i in ids])
    print(f"\n1. 모양 ({n}장, 재투영 중앙 {np.median(list(err.values())):.2f} px)")
    check("모든 마커를 찾음", sorted(W) == ids, f"{sorted(W)}")
    e = np.linalg.norm(kabsch(A, B) - B, axis=1) * 1000
    check("강체 정합 후 코너 잔차 < 1.5 mm", e.max() < 1.5, f"중앙 {np.median(e):.2f} / 최대 {e.max():.2f} mm")

    print("\n2. W 규칙")
    c0 = W[0].mean(0)
    check("ID 0 이 앞-왼 (0.07, 0.07) 근처", np.linalg.norm(c0[:2] - [0.07, 0.07]) < 0.03,
          f"({c0[0]:.3f}, {c0[1]:.3f}, {c0[2]:.4f})")
    d = W[2].mean(0) - W[0].mean(0)                      # 정답에서 0 → 2 는 +X 방향
    ang = np.degrees(np.arctan2(d[1], d[0]))
    check("X 축이 판 앞 변과 나란함 (< 2°)", abs(ang) < 2, f"0→2 방향 {ang:+.2f}°")
    check("마커 면이 Z=0", np.abs(A[:, 2]).max() < 0.002, f"|Z| 최대 {np.abs(A[:, 2]).max() * 1000:.2f} mm")

    print("\n3. 축척")
    span = np.linalg.norm(W[4].mean(0) - W[0].mean(0)); span99 = np.linalg.norm(W99[4].mean(0) - W99[0].mean(0))
    check("한 변 1% 작게 → 배치도 1% 작게", abs(span99 / span - 0.99) < 0.001,
          f"대각 {span:.4f} → {span99:.4f} m ({(span99 / span - 1) * 100:+.2f}%)")

    n_ok = sum(_results)
    print("\n" + "=" * 60)
    print(f"{n_ok}/{len(_results)} 통과" + ("" if n_ok == len(_results) else f"  — {len(_results) - n_ok}건 실패"))


if __name__ == "__main__":
    main()
