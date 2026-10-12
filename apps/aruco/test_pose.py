"""pose.py 검증 — 갠트리 궤도 합성 화면으로.

실제 캘리브레이션 값(camera_1280x960.json)의 카메라를 궤도(반지름 1.055m, 판 위 1.114m,
판 중심 조준) 24곳에 두고, 판 + 마커 8개(markers_W.json)를 픽셀 단위로 렌더링한다.
PoseEstimator 가 정답 자세를 되찾으면 통과.

마지막 항목은 **캘리브레이션 불확실성의 영향**이다. 부트스트랩으로 잰 흔들림만큼
K 를 틀리게 준 추정기로 자세를 풀고, 판 위 점(요구조자 자리)을 역투영해 실제 위치와
비교한다. 카메라 위치가 틀려도 마커와 대상이 같은 화면에 있으면 오차가 상당 부분 상쇄된다.

    uv run python test_pose.py
"""
from __future__ import annotations

import cv2
import numpy as np

import pose as P
from align import ARUCO_DICT

W, H = 1280, 960
K, D = P.load_camera()
LAYOUT = P.load_layout()
SIZE = float(np.linalg.norm(LAYOUT[0][1] - LAYOUT[0][0]))
PLANE_Z = float(LAYOUT[0][0, 2])            # 마커 표면 높이. 렌더링은 판 전체를 이 평면으로 본다
CENTER = np.array([0.6, 0.6, 0.0])
ORBIT_R, ORBIT_Z = 1.055, 1.114
CRIT = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 50, 1e-10)   # 기본 5회면 구석이 틀린다
PASS, FAIL = "\033[32m통과\033[0m", "\033[31m실패\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


_uv = np.stack(np.meshgrid(np.arange(W), np.arange(H)), -1).reshape(-1, 1, 2).astype(np.float64)
RAYS = np.hstack([cv2.undistortPoints(_uv, K, D, criteria=CRIT).reshape(-1, 2), np.ones((W * H, 1))])
BITS = {i: cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(ARUCO_DICT), i, 600)
        for i in LAYOUT}


def look_at(C: np.ndarray, target: np.ndarray) -> np.ndarray:
    """W → 카메라 회전. 카메라 축: x 오른쪽, y 아래, z 앞."""
    z = (target - C) / np.linalg.norm(target - C)
    x = np.cross(z, [0, 0, 1.0]); x /= np.linalg.norm(x)
    return np.stack([x, np.cross(z, x), z])


def render(R: np.ndarray, C: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    d = RAYS @ R                                     # 광선을 W 로
    s = (PLANE_Z - C[2]) / d[:, 2]
    X, Y = C[0] + s * d[:, 0], C[1] + s * d[:, 1]
    img = np.full(len(X), 170.0)                     # 받침대
    on = (X >= 0) & (X <= 1.2) & (Y >= 0) & (Y <= 1.2)
    img[on] = 95 + 25 * np.sin(X[on] * 180) * np.sin(Y[on] * 150)   # 잔디 무늬 — 균일하면 검출이 쉬워진다
    for i, c4 in LAYOUT.items():
        x0, y1 = c4[0, 0], c4[0, 1]                  # 좌상 코너 (위쪽 = +Y)
        m = (X > x0 - 0.02) & (X < x0 + SIZE + 0.02) & (Y > y1 - SIZE - 0.02) & (Y < y1 + 0.02)
        img[m] = 235.0                               # 흰 여백 2cm
        u, v = (X - x0) / SIZE * 600, (y1 - Y) / SIZE * 600
        k = m & (u >= 0) & (u < 600) & (v >= 0) & (v < 600)
        img[k] = np.where(BITS[i][v[k].astype(int), u[k].astype(int)] > 127, 235.0, 25.0)
    img[s <= 0] = 60.0
    img = cv2.GaussianBlur(img.reshape(H, W), (0, 0), 0.9) + rng.normal(0, 2.0, (H, W))
    return np.clip(img, 0, 255).astype(np.uint8)


def backproject(uv: np.ndarray, pose: P.Pose, K_: np.ndarray, D_: np.ndarray) -> np.ndarray:
    """픽셀 → 광선 → 평면 Z=PLANE_Z 교점 (W). 탐지 역투영의 평면 버전."""
    x = cv2.undistortPoints(uv.reshape(-1, 1, 2), K_, D_, criteria=CRIT).reshape(-1, 2)
    d = np.hstack([x, np.ones((len(x), 1))]) @ pose.R
    s = (PLANE_Z - pose.C[2]) / d[:, 2]
    return pose.C + s[:, None] * d


def main() -> None:
    rng = np.random.default_rng(0)
    est = P.PoseEstimator(K, D, LAYOUT)
    print(f"궤도 24곳 렌더링 (반지름 {ORBIT_R}m, 높이 {ORBIT_Z}m, fx {K[0, 0]:.0f}) …")
    frames = []
    for th in np.linspace(0, 2 * np.pi, 24, endpoint=False):
        C = CENTER + [ORBIT_R * np.cos(th), ORBIT_R * np.sin(th), ORBIT_Z]
        R = look_at(C, CENTER)
        frames.append((th, R, C, render(R, C, rng)))

    n_seen, pos_err, rot_err, reproj, floor = [], [], [], [], []
    for th, R, C, img in frames:
        seen = est.detect(img)
        p = est.estimate(seen)
        n_seen.append(0 if p is None else len(p.ids))
        if p is None:
            continue
        pos_err.append(np.linalg.norm(p.C - C) * 1000)
        rot_err.append(np.degrees(np.arccos(np.clip((np.trace(p.R @ R.T) - 1) / 2, -1, 1))))
        reproj.append(p.err_px)
        # 정답 자세로 재투영한 오차 = 코너 검출 잡음의 바닥 (렌더링 앨리어싱 + 검출기)
        obj = np.vstack([LAYOUT[i] for i in p.ids]); img_pts = np.vstack([seen[i] for i in p.ids])
        pr = cv2.projectPoints(obj, cv2.Rodrigues(R)[0], (-R @ C).reshape(3, 1), K, D)[0].reshape(-1, 2)
        floor.append(float(np.sqrt(np.mean(np.sum((pr - img_pts) ** 2, 1)))))
    n_seen = np.array(n_seen)

    print("\n1. 검출")
    check("모든 프레임에서 자세를 풂", len(pos_err) == len(frames), f"{len(pos_err)}/{len(frames)}")
    check("프레임당 마커 >= 4 (가시성 시뮬레이션과 일치)", n_seen.min() >= 4,
          f"최소 {n_seen.min()} · 평균 {n_seen.mean():.1f} · 최대 {n_seen.max()}")

    print("\n2. 자세 정확도 (카메라 = 정답)")
    check("카메라 위치 오차 < 10 mm", max(pos_err) < 10,
          f"중앙 {np.median(pos_err):.1f} / 최대 {max(pos_err):.1f} mm")
    check("카메라 회전 오차 < 0.3°", max(rot_err) < 0.3,
          f"중앙 {np.median(rot_err):.3f} / 최대 {max(rot_err):.3f}°")
    # 재투영 오차의 절대값은 입력 잡음이 정한다. 풀이가 할 일은 정답 자세보다 나쁘지 않은 것
    check("재투영 RMS <= 정답 자세의 재투영 (최소제곱 수렴)",
          all(r <= f + 1e-3 for r, f in zip(reproj, floor)),
          f"추정 최대 {max(reproj):.2f} px · 정답 자세 {min(floor):.2f}~{max(floor):.2f} px (코너 잡음 바닥)")

    print("\n3. 입력 방어")
    check("배치표에 없는 ID 는 버린다", all(i in LAYOUT for i in est.detect(frames[0][3])))
    blank = np.full((H, W), 128, np.uint8)
    check("마커가 없으면 None", est(blank) is None)

    # 4. 캘리브레이션이 부트스트랩 흔들림만큼 틀렸을 때 — 판 위 점을 역투영해 실제 위치와 비교
    print("\n4. 캘리브레이션 불확실성의 영향 (fx +5.6px · cx +7.4px · cy +8.9px 로 틀리게 준 K)")
    Kb = K.copy(); Kb[0, 0] += 5.6; Kb[1, 1] += 5.6; Kb[0, 2] += 7.4; Kb[1, 2] += 8.9
    bad = P.PoseEstimator(Kb, D, LAYOUT)
    targets = np.array([[x, y, PLANE_Z] for x in np.linspace(0.2, 1.0, 5) for y in np.linspace(0.2, 1.0, 5)])
    cam_err, pt_err = [], []
    for th, R, C, img in frames:
        p = bad(img)
        cam_err.append(np.linalg.norm(p.C - C) * 1000)
        uv, _ = cv2.projectPoints(targets, cv2.Rodrigues(R)[0], (-R @ C).reshape(3, 1), K, D)
        uv = uv.reshape(-1, 2)
        vis = (uv[:, 0] > 0) & (uv[:, 0] < W) & (uv[:, 1] > 0) & (uv[:, 1] < H)
        pt_err += list(np.linalg.norm(backproject(uv[vis], p, Kb, D) - targets[vis], axis=1) * 1000)
    print(f"  [정보] 카메라 위치 오차 중앙 {np.median(cam_err):.0f} / 최대 {max(cam_err):.0f} mm")
    check("판 위 점(요구조자 자리) 역투영 오차 < 15 mm", np.quantile(pt_err, 0.95) < 15,
          f"중앙 {np.median(pt_err):.1f} / 95% {np.quantile(pt_err, 0.95):.1f} / 최대 {max(pt_err):.1f} mm")

    n = sum(_results)
    print("\n" + "=" * 60)
    print(f"{n}/{len(_results)} 통과" + ("" if n == len(_results) else f"  — {len(_results) - n}건 실패"))


if __name__ == "__main__":
    main()
