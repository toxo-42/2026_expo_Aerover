"""calibrate.py 검증 — 정답을 아는 합성 사진으로.

aerover 번들조정값(초점 1348px, k1 -0.46 광각)을 정답으로 두고, 그 카메라로 찍은 것처럼
체스보드 사진을 픽셀 단위로 렌더링한다. calibrate() 가 정답을 되찾으면 통과.

렌더링은 역방향이다 — 출력 픽셀마다 왜곡을 풀어 광선을 만들고, 보드 평면과 만난 점의
색(흑/백/종이 밖)을 칠한다. 그래서 왜곡 모델이 OpenCV 정의와 정확히 같다.

    uv run python test_calibrate.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import numpy as np

import calibrate as cal

W, H = cal.STREAM_SIZE
K_TRUE = np.array([[1348.0, 0, 642.0], [0, 1348.0, 478.0], [0, 0, 1]])
D_TRUE = np.array([-0.461, 0.0359, 0.0227, -0.0012, 0.049])
COLS, ROWS, SQ = 9, 6, 0.020
PASS, FAIL = "\033[32m통과\033[0m", "\033[31m실패\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


# undistortPoints 는 반복법이다. 기본값(5회)이면 k1 -0.46 에서 화면 구석이 22px 틀린다 — 50회로 준다
CRIT = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 50, 1e-10)

# 픽셀마다 왜곡을 푼 정규화 좌표 — 모든 장이 같은 카메라라 한 번만 계산한다
_uv = np.stack(np.meshgrid(np.arange(W), np.arange(H)), -1).reshape(-1, 1, 2).astype(np.float64)
RAYS = cv2.undistortPoints(_uv, K_TRUE, D_TRUE, criteria=CRIT).reshape(-1, 2)


def render(rvec: np.ndarray, tvec: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """보드 좌표계 원점 = 첫 내부 코너. 사각은 (COLS+1)x(ROWS+1), 좌상단 사각이 검정."""
    R, _ = cv2.Rodrigues(rvec)
    d = np.hstack([RAYS, np.ones((len(RAYS), 1))]) @ R          # 광선을 보드 좌표계로
    o = -R.T @ tvec.ravel()                                       # 카메라 중심 (보드 좌표계)
    s = -o[2] / d[:, 2]
    X, Y = o[0] + s * d[:, 0], o[1] + s * d[:, 1]
    i, j = np.floor(X / SQ).astype(int) + 1, np.floor(Y / SQ).astype(int) + 1
    on_board = (i >= 0) & (i <= COLS) & (j >= 0) & (j <= ROWS) & (s > 0)
    on_paper = (X > -2 * SQ) & (X < (COLS + 1) * SQ) & (Y > -2 * SQ) & (Y < (ROWS + 1) * SQ) & (s > 0)
    img = np.full(len(X), 90.0)                                   # 종이 밖 = 회색 배경
    img[on_paper] = 235.0
    img[on_board & ((i + j) % 2 == 0)] = 20.0
    img = img.reshape(H, W)
    img = cv2.GaussianBlur(img, (0, 0), 0.8) + rng.normal(0, 2.0, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def random_poses(n: int, rng: np.random.Generator) -> list[tuple[np.ndarray, np.ndarray]]:
    """화면 곳곳(구석 포함)에 보드가 다 들어오는 자세만 고른다.

    가까이서만 찍으면 보드가 커서 구석에 닿지 못한다 — 멀리(작게) 둔 장이 구석을 맡는다.
    """
    obj = cal.board_points(COLS, ROWS, SQ).astype(np.float64)
    center = obj.mean(0)
    poses = []
    while len(poses) < n:
        rvec = np.radians(rng.uniform([-35, -35, -20], [35, 35, 20]))
        R, _ = cv2.Rodrigues(rvec)
        z = rng.uniform(0.30, 0.90)
        target = np.array([rng.uniform(-0.5, 0.5) * W / 1348 * z,
                           rng.uniform(-0.5, 0.5) * H / 1348 * z, z])
        tvec = target - R @ center
        uv, _ = cv2.projectPoints(obj, rvec, tvec, K_TRUE, D_TRUE)
        uv = uv.reshape(-1, 2)
        if (uv.min(0) > 10).all() and (uv[:, 0].max() < W - 10) and (uv[:, 1].max() < H - 10):
            poses.append((rvec, tvec))
    return poses


def main() -> None:
    rng = np.random.default_rng(0)
    print("합성 체스보드 20장 렌더링 (1280x960, 초점 1348px, k1 -0.461) …")
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for k, (rv, tv) in enumerate(random_poses(20, rng)):
            p = Path(tmp) / f"{k:03d}.png"
            cv2.imwrite(str(p), render(rv, tv, rng))
            paths.append(p)
        res = cal.calibrate(paths, COLS, ROWS, SQ)

    K, d = np.array(res["K"]), np.array(res["dist"])
    print("\n1. 검출")
    check("체스보드 검출 장수", res["n_used"] >= 18, f"{res['n_used']}/20")
    check("가장자리 커버", res["coverage"] >= cal.COVER_OK, f"{res['coverage']}/{cal.GRID ** 2}칸")
    # 합성 자세의 성질이라 통과 조건이 아니다 — 아래 외삽 구간 크기를 읽는 기준으로만 찍는다
    print(f"  [정보] 구석 도달 — 구석 반경의 {res['reach'] * 100:.1f}% (calibrate 판정 기준 {cal.REACH_OK * 100:.0f}%)")

    print("\n2. 정답 복원")
    check("재투영 RMS < 0.5 px", res["rms_px"] < 0.5, f"{res['rms_px']:.3f} px")
    ef = abs(K[0, 0] - K_TRUE[0, 0]) / K_TRUE[0, 0] * 100
    check("초점거리 오차 < 0.5%", ef < 0.5, f"{K[0, 0]:.1f} vs {K_TRUE[0, 0]:.0f} ({ef:.3f}%)")
    ec = np.hypot(K[0, 2] - K_TRUE[0, 2], K[1, 2] - K_TRUE[1, 2])
    check("주점 오차 < 3 px", ec < 3, f"({K[0, 2]:.1f}, {K[1, 2]:.1f}) vs (642, 478), {ec:.2f} px")

    # 왜곡은 계수끼리 서로 보상하므로 계수 대신 **결과 픽셀**로 비교한다.
    # 화면 전체 픽셀에 해당하는 광선을 두 모델로 투영한다 (solvePnP 가 쓰는 방향).
    # 주점 어긋남(위에서 따로 잰다)이 섞이지 않게 각자의 주점에서 잰 상대 위치로 비교한다
    # 데이터(코너)가 닿은 반경 안만 판정한다. 그 바깥은 외삽이라 오차가 급히 커진다
    step = 97
    rays = np.hstack([RAYS[::step], np.ones((len(RAYS[::step]), 1))])
    a = cv2.projectPoints(rays, np.zeros(3), np.zeros(3), K_TRUE, D_TRUE)[0].reshape(-1, 2) - K_TRUE[:2, 2]
    b = cv2.projectPoints(rays, np.zeros(3), np.zeros(3), K, d)[0].reshape(-1, 2) - K[:2, 2]
    e = np.linalg.norm(a - b, axis=1)
    pr = np.linalg.norm(_uv.reshape(-1, 2)[::step] - [W / 2, H / 2], axis=1) / np.hypot(W / 2, H / 2)
    inside = pr <= res["reach"]
    check("투영 결과 차이 (데이터가 닿은 반경 안) < 1 px", e[inside].max() < 1.0,
          f"중앙 {np.median(e[inside]):.3f} / 최대 {e[inside].max():.3f} px, "
          f"k1 {d[0]:.4f} · k3 {d[4]:.4f} vs {D_TRUE[0]} · {D_TRUE[4]}")
    print(f"  [정보] 외삽 구간 (화면의 {np.mean(~inside) * 100:.1f}%) 최대 {e[~inside].max():.2f} px"
          " — 실제 촬영에서 보드를 구석까지 찍어야 하는 이유")

    # 반대 방향(픽셀 → 광선)은 탐지 역투영이 쓴다. 추정 모델로 구석 픽셀을 풀었다 되감아 본다
    g = np.stack(np.meshgrid(np.linspace(0, W - 1, 17), np.linspace(0, H - 1, 13)), -1).reshape(-1, 1, 2)
    x = cv2.undistortPoints(g, K, d, criteria=CRIT).reshape(-1, 2)
    back = cv2.projectPoints(np.hstack([x, np.ones((len(x), 1))]), np.zeros(3), np.zeros(3), K, d)[0]
    e = np.linalg.norm(back.reshape(-1, 2) - g.reshape(-1, 2), axis=1)
    check("픽셀→광선→픽셀 왕복 (50회 반복) < 0.1 px", e.max() < 0.1, f"최대 {e.max():.4f} px")

    print("\n3. 입력 방어")
    with tempfile.TemporaryDirectory() as tmp:
        p1, p2 = Path(tmp) / "a.png", Path(tmp) / "b.png"
        cv2.imwrite(str(p1), np.zeros((H, W), np.uint8))
        cv2.imwrite(str(p2), np.zeros((720, 1280), np.uint8))
        try:
            cal.calibrate([p1, p2], COLS, ROWS, SQ)
            check("해상도가 섞이면 거부", False)
        except ValueError as ex:
            check("해상도가 섞이면 거부", "섞였다" in str(ex), str(ex))

    n = sum(_results)
    print("\n" + "=" * 60)
    print(f"{n}/{len(_results)} 통과" + ("" if n == len(_results) else f"  — {len(_results) - n}건 실패"))


if __name__ == "__main__":
    main()
