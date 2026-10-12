"""드론 카메라 캘리브레이션 — 체스보드 사진 폴더 → K · 왜곡 json.

solvePnP 는 내부 파라미터를 반드시 요구한다. `aerover/cameras_imx477_6mm.json` 은
ODM 번들조정 수렴값이라 실시간 자세 추정에 그대로 믿고 쓰기 어렵다 (ArUco_준비작업.md 7절).

    uv run python calibrate.py ../aerover/sessions/<회차>
    uv run python calibrate.py <폴더> --square 19.8          # 실측한 사각 한 변(mm)
    uv run python calibrate.py <폴더> --preview undist.jpg   # 왜곡을 편 사진으로 눈 확인

흐름:

    사진마다 체스보드 내부 코너 검출 (findChessboardCornersSB — 서브픽셀까지 한 번에)
      → 보드 좌표(사각 한 변 × 격자)와 짝지어 cv2.calibrateCamera (k3 고정 → k3 포함, 2단계)
      → K (fx, fy, cx, cy) · dist (k1, k2, p1, p2, k3) · 재투영 오차

판정 기준 (터미널에 같이 찍는다):
  - 재투영 RMS < 0.5 px
  - 쓴 장수 >= 15
  - 화면 4x4 칸 중 코너가 닿은 칸 >= 14 — 왜곡계수는 **가장자리**에서 나온다
  - 코너가 닿은 최대 반경 >= 화면 구석 반경의 90% — 칸은 다 차도 맨 구석이 비면
    그 바깥은 외삽이다 (합성 시험: 90% 까지 찍으면 안쪽 최대 0.75px, 바깥 구석은 최대 6.8px)
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import cv2
import numpy as np

STREAM_SIZE = (1280, 960)       # pi_code/src/camera/settings.py StreamCameraSettings.size
AEROVER_CAM = Path(__file__).parent / "../aerover/cameras_imx477_6mm.json"
SB_FLAGS = cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
GRID = 4                        # 커버리지 판정 격자 (GRID x GRID)

RMS_OK, VIEWS_OK, COVER_OK, REACH_OK = 0.5, 15, 14, 0.9


def board_points(cols: int, rows: int, square_m: float) -> np.ndarray:
    """보드 평면 위 내부 코너의 3D 좌표 (Z=0). findChessboardCorners 의 순서와 같다."""
    g = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    return np.hstack([g * square_m, np.zeros((len(g), 1))]).astype(np.float32)


def find_corners(gray: np.ndarray, cols: int, rows: int) -> np.ndarray | None:
    ok, c = cv2.findChessboardCornersSB(gray, (cols, rows), SB_FLAGS)
    return c.reshape(-1, 2).astype(np.float32) if ok else None


def coverage(corners: list[np.ndarray], size: tuple[int, int]) -> int:
    """화면을 GRID x GRID 로 나눠 코너가 하나라도 닿은 칸 수."""
    w, h = size
    pts = np.vstack(corners)
    cx = np.clip((pts[:, 0] * GRID / w).astype(int), 0, GRID - 1)
    cy = np.clip((pts[:, 1] * GRID / h).astype(int), 0, GRID - 1)
    return len(set(zip(cx.tolist(), cy.tolist())))


def reach(corners: list[np.ndarray], size: tuple[int, int]) -> float:
    """화면 중심에서 가장 먼 코너까지의 거리 / 화면 구석까지의 거리."""
    w, h = size
    pts = np.vstack(corners)
    return float(np.hypot(pts[:, 0] - w / 2, pts[:, 1] - h / 2).max() / np.hypot(w / 2, h / 2))


def calibrate(images: list[Path], cols: int, rows: int, square_m: float) -> dict:
    obj = board_points(cols, rows, square_m)
    found: dict[str, np.ndarray] = {}
    size = None
    for p in images:
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        s = (img.shape[1], img.shape[0])
        # 해상도가 섞이면 K 가 뜻을 잃는다 — 스트림과 다른 크기는 받지 않는다
        if size is None:
            size = s
        elif s != size:
            raise ValueError(f"{p.name}: {s} — 앞 사진들은 {size}. 해상도가 섞였다")
        c = find_corners(img, cols, rows)
        if c is not None:
            found[p.name] = c
    if size is None:
        raise ValueError("읽을 수 있는 사진이 없다")
    if len(found) < 3:
        raise ValueError(f"체스보드를 찾은 사진이 {len(found)}장뿐이다 (최소 3, 권장 {VIEWS_OK})")

    names = list(found)
    objs, imgs = [obj] * len(names), [found[n] for n in names]
    # 2단계로 푼다. k3 까지 한 번에 풀면 k1 -0.46 광각에서 국소 최소에 빠질 때가 있다
    # (합성 시험: 주점 32px 어긋남, rms 0.42 — k3 를 고정한 해 0.106 보다 나빴다).
    # k3 없이 먼저 수렴시키고 그 값을 초기값으로 k3 까지 다시 푼다.
    _, K, dist, _, _ = cv2.calibrateCamera(objs, imgs, size, None, None, flags=cv2.CALIB_FIX_K3)
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(
        objs, imgs, size, K, dist, flags=cv2.CALIB_USE_INTRINSIC_GUESS)

    per_image = {}
    for n, rv, tv in zip(names, rvecs, tvecs):
        proj, _ = cv2.projectPoints(obj, rv, tv, K, dist)
        per_image[n] = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - found[n]) ** 2, 1))))

    return {
        "date": date.today().isoformat(),
        "image_size": list(size),
        "board": {"cols": cols, "rows": rows, "square_m": square_m},
        "K": K.tolist(),
        "dist": dist.ravel().tolist(),          # OpenCV 순서: k1, k2, p1, p2, k3
        "rms_px": float(rms),
        "n_used": len(names),
        "n_total": len(images),
        "coverage": coverage(list(found.values()), size),
        "reach": reach(list(found.values()), size),
        "per_image_px": per_image,
    }


def report(res: dict) -> None:
    K, d = np.array(res["K"]), res["dist"]
    w, h = res["image_size"]
    print(f"\n사진 {res['n_used']}/{res['n_total']}장에서 체스보드 검출, 해상도 {w}x{h}")
    print(f"  fx {K[0, 0]:.1f}  fy {K[1, 1]:.1f}  cx {K[0, 2]:.1f}  cy {K[1, 2]:.1f}")
    print("  k1 {:.4f}  k2 {:.4f}  p1 {:.5f}  p2 {:.5f}  k3 {:.4f}".format(*d[:5]))

    print("\n장별 재투영 오차 (큰 순 5장 — 튀는 장은 지우고 다시 돌린다)")
    for n, e in sorted(res["per_image_px"].items(), key=lambda x: -x[1])[:5]:
        print(f"  {e:6.3f} px  {n}")

    print("\n판정")
    for name, ok, detail in [
        ("재투영 RMS", res["rms_px"] < RMS_OK, f"{res['rms_px']:.3f} px (기준 < {RMS_OK})"),
        ("쓴 장수", res["n_used"] >= VIEWS_OK, f"{res['n_used']}장 (기준 >= {VIEWS_OK})"),
        ("가장자리 커버", res["coverage"] >= COVER_OK,
         f"{res['coverage']}/{GRID * GRID}칸 (기준 >= {COVER_OK})"),
        ("구석 도달", res["reach"] >= REACH_OK,
         f"화면 구석 반경의 {res['reach'] * 100:.0f}% (기준 >= {REACH_OK * 100:.0f}%) — "
         "미달이면 보드를 작게(멀리) 두고 네 구석에 걸쳐 더 찍는다"),
        ("스트림 해상도", tuple(res["image_size"]) == STREAM_SIZE, f"{w}x{h} (스트림 {STREAM_SIZE})"),
    ]:
        print(f"  [{'통과' if ok else '미달'}] {name} — {detail}")

    # 번들조정값과 비교. OpenSfM 초점은 max(w, h) 로 나눈 값이다
    if AEROVER_CAM.exists():
        ba = next(iter(json.loads(AEROVER_CAM.read_text()).values()))
        print(f"\n참고 — aerover 번들조정값: 초점 {ba['focal_x'] * max(w, h):.1f}px, k1 {ba['k1']:.4f}"
              f"  (이번 값: {K[0, 0]:.1f}px, k1 {d[0]:.4f})")


def main() -> None:
    ap = argparse.ArgumentParser(description="체스보드 사진 폴더 → 카메라 내부 파라미터")
    ap.add_argument("folder", type=Path, help="체스보드 사진 폴더 (aerover sessions/<회차> 등)")
    ap.add_argument("--cols", type=int, default=9, help="내부 코너 가로 수")
    ap.add_argument("--rows", type=int, default=6, help="내부 코너 세로 수")
    ap.add_argument("--square", type=float, default=20.0, help="사각 한 변 실측값 (mm)")
    ap.add_argument("--out", type=Path, default=Path("camera_1280x960.json"))
    ap.add_argument("--preview", type=Path, help="첫 장의 왜곡을 편 사진을 여기 저장")
    a = ap.parse_args()

    images = sorted(p for p in a.folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    res = calibrate(images, a.cols, a.rows, a.square / 1000)
    report(res)
    a.out.write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(f"\n저장: {a.out}")

    if a.preview:
        img = cv2.imread(str(images[0]))
        a.preview.write_bytes(cv2.imencode(".jpg", cv2.undistort(
            img, np.array(res["K"]), np.array(res["dist"])))[1].tobytes())
        print(f"미리보기: {a.preview} — 체스보드 · 판 변 같은 직선이 곧게 펴졌는지 본다")


if __name__ == "__main__":
    main()
