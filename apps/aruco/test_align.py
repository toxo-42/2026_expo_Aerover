"""align.py 검증 — 마커 없이 할 수 있는 데까지.

실물 ArUco 촬영본이 없으므로, 이미 있는 1차-B 재구성(112장)을 정답지로 쓴다.
검증하지 못하는 것은 **ArUco 코너 검출 정확도 하나뿐**이고, 그건 마커 시트를
만들 때 따로 쟀다 (10cm 마커에서 1.4mm — ArUco_준비작업.md 2절).

    uv run python test_align.py
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

import align

DATA = Path("data")
IMAGES = Path("../ODM_testing/images_sub")
PASS, FAIL = "\033[32m통과\033[0m", "\033[31m실패\033[0m"
_results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  — {detail}" if detail else ""))


def load():
    cam = align.Camera.load(DATA / "cameras.json")
    shots = align.load_shots(DATA / "shots.geojson")
    rec = json.loads((DATA / "reconstruction.json").read_text())[0]
    return cam, shots, rec


# ── 1. 포즈 규약 ──────────────────────────────────────────────────────────

def test_pose_convention(shots, rec):
    print("\n1. 포즈 규약 — shots.geojson 의 translation 은 무엇인가")
    ec, et = [], []
    for n, s in rec["shots"].items():
        R, _ = cv2.Rodrigues(np.array(s["rotation"]))
        t = np.array(s["translation"])
        _, C = shots[n]
        ec.append(np.abs(-R.T @ t - C).max())
        et.append(np.abs(t - C).max())
    check("카메라 위치 C 와 일치", max(ec) < 1e-9, f"최대오차 {max(ec):.2e}")
    check("t 벡터와는 불일치 (혼동 방지)", max(et) > 1.0, f"최대차 {max(et):.2f}")
    check("112장 모두 존재", len(shots) == 112 and len(rec["shots"]) == 112)


# ── 2. 투영 ───────────────────────────────────────────────────────────────

def test_projection(cam, shots, rec):
    print("\n2. 투영 — OpenCV 등가 카메라가 OpenSfM 정의와 같은가")
    c = next(iter(rec["cameras"].values()))
    W, H, s = c["width"], c["height"], max(c["width"], c["height"])
    pts = rec["points"]
    keys = list(pts)[:2000]
    Xw = np.array([pts[k]["coordinates"] for k in keys])
    R, C = shots[next(iter(shots))]

    # OpenSfM 정의를 손으로 편 것
    Xc = (R @ (Xw - C).T).T
    xn, yn = Xc[:, 0] / Xc[:, 2], Xc[:, 1] / Xc[:, 2]
    r2 = xn * xn + yn * yn
    rad = 1 + c["k1"] * r2 + c["k2"] * r2 ** 2 + c["k3"] * r2 ** 3
    xd = xn * rad + 2 * c["p1"] * xn * yn + c["p2"] * (r2 + 2 * xn * xn)
    yd = yn * rad + c["p1"] * (r2 + 2 * yn * yn) + 2 * c["p2"] * xn * yn
    ref = np.stack([c["focal_x"] * xd + c["c_x"], c["focal_y"] * yd + c["c_y"]], 1) * s \
        + np.array([W / 2 - 0.5, H / 2 - 0.5])

    uv, _ = cam.project(Xw, R, C)
    check("OpenSfM 수식과 일치", np.abs(uv - ref).max() < 1e-6,
          f"최대오차 {np.abs(uv - ref).max():.2e} px")

    back = cam.undistort(uv)
    check("undistort 왕복", np.abs(back - np.stack([xn, yn], 1)).max() < 1e-4,
          f"최대오차 {np.abs(back - np.stack([xn, yn], 1)).max():.2e}")


def test_projection_vs_images(cam, shots, rec):
    """점의 색이 투영 위치의 픽셀 색과 맞는가. 규약이 틀리면 최소점이 밀린다."""
    print("\n3. 투영 vs 실제 이미지 — 색 일치의 최소점이 (0,0) 인가")
    pts = rec["points"]
    keys = list(pts)
    rng = np.random.default_rng(1)
    sel = [keys[i] for i in rng.choice(len(keys), 6000, replace=False)]
    Xw = np.array([pts[k]["coordinates"] for k in sel])
    col = np.array([pts[k]["color"] for k in sel])

    offs = np.arange(-8, 9, 4)
    acc, n = np.zeros((len(offs), len(offs))), 0
    for sn in list(rec["shots"])[:10]:
        img = cv2.imread(str(IMAGES / sn))
        if img is None:
            continue
        img = img[:, :, ::-1].astype(np.float32)
        R, C = shots[sn]
        uv, z = cam.project(Xw, R, C)
        m = int(offs.max()) + 2
        ok = (z > 0) & (uv[:, 0] > m) & (uv[:, 0] < cam.width - m) \
             & (uv[:, 1] > m) & (uv[:, 1] < cam.height - m)
        if ok.sum() < 50:
            continue
        p, cc = uv[ok].round().astype(int), col[ok]
        for i, dv in enumerate(offs):
            for j, du in enumerate(offs):
                acc[i, j] += np.median(np.abs(img[p[:, 1] + dv, p[:, 0] + du] - cc).mean(1))
        n += 1
    acc /= max(n, 1)
    i, j = np.unravel_index(acc.argmin(), acc.shape)
    check("색 오차 최소점 = (0,0)", offs[j] == 0 and offs[i] == 0,
          f"최소 du={offs[j]}, dv={offs[i]}, 값 {acc[i, j]:.1f} / {n}장")


# ── 4. 삼각측량 ───────────────────────────────────────────────────────────

def test_triangulation(cam, shots, rec):
    print("\n4. 삼각측량 — 투영했다 되돌리면 원래 점이 나오는가")
    pts = rec["points"]
    keys = list(pts)
    rng = np.random.default_rng(2)
    sel = [keys[i] for i in rng.choice(len(keys), 300, replace=False)]
    names = list(shots)

    for noise, tol in [(0.0, 1e-6), (1.0, 0.05)]:
        errs, nv = [], []
        for k in sel:
            X = np.array(pts[k]["coordinates"])
            obs = []
            for n in names:
                R, C = shots[n]
                uv, z = cam.project(X, R, C)
                if z[0] <= 0 or not (0 <= uv[0, 0] < cam.width and 0 <= uv[0, 1] < cam.height):
                    continue
                p = uv[0] + rng.normal(0, noise, 2)
                obs.append((R, C, cam.undistort(p)[0]))
            if len(obs) < 3:
                continue
            nv.append(len(obs))
            errs.append(np.linalg.norm(align.triangulate(obs) - X))
        errs = np.array(errs)
        label = "잡음 없음" if noise == 0 else f"픽셀잡음 σ={noise}px"
        check(f"복원 오차 ({label})", np.median(errs) < tol,
              f"중앙 {np.median(errs):.2e} / 최대 {errs.max():.2e} 단위, "
              f"평균 {np.mean(nv):.0f}장 관측, n={len(errs)}")


# ── 5. 유사변환 ───────────────────────────────────────────────────────────

def test_umeyama():
    print("\n5. Umeyama — 알려진 변환을 복원하는가")
    rng = np.random.default_rng(3)
    s_true = 0.0813
    R_true, _ = cv2.Rodrigues(np.array([0.31, -0.72, 1.14]))
    t_true = np.array([1.7, -0.4, 0.06])
    src = rng.normal(0, 8, (24, 3))
    dst = align.apply(s_true, R_true, t_true, src)

    s, R, t = align.umeyama(src, dst)
    check("축척", abs(s - s_true) / s_true < 1e-9, f"{s:.9f} vs {s_true}")
    check("회전", np.abs(R - R_true).max() < 1e-9)
    check("이동", np.abs(t - t_true).max() < 1e-9)

    # 반사(거울상)를 해로 뽑지 않는가 — 좌표계가 뒤집히면 전부 무의미해진다
    mirrored = dst * np.array([1, 1, -1])
    _, Rm, _ = align.umeyama(src, mirrored)
    check("반사 해를 만들지 않는다", np.linalg.det(Rm) > 0, f"det={np.linalg.det(Rm):+.6f}")

    # 잡음 내성
    dst_n = dst + rng.normal(0, 0.002, dst.shape)
    s2, R2, t2 = align.umeyama(src, dst_n)
    check("잡음 2mm 에서 축척 오차 <0.5%", abs(s2 - s_true) / s_true < 0.005,
          f"{abs(s2 - s_true) / s_true * 100:.3f}%")


# ── 6. 실측표 → 코너 ──────────────────────────────────────────────────────

def test_world_corners():
    print("\n6. 실측표 → 코너 좌표")
    c = np.array([0.6, 0.6, 0.005])
    q = align.world_corners(c, 0.10, "+Y")
    d = [np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4)]
    check("변 길이 = 마커 크기", np.allclose(d, 0.10), f"{np.round(d, 4)}")
    check("중심 보존", np.allclose(q.mean(0), c))
    check("좌상→우상이 +X (up=+Y)", np.allclose(q[1] - q[0], [0.10, 0, 0]))
    q2 = align.world_corners(c, 0.10, "+X")
    check("up=+X 면 좌상→우상이 -Y", np.allclose(q2[1] - q2[0], [0, -0.10, 0]))


# ── 7. 전체 흐름 (합성 마커) ──────────────────────────────────────────────

def test_end_to_end(cam, shots, rec):
    """가짜 마커를 ODM 좌표계에 놓고 전체 파이프라인을 돌려 변환을 되찾는가."""
    print("\n7. 전체 흐름 — 합성 마커로 변환을 되찾는가")
    rng = np.random.default_rng(4)

    # 실제 디오라마가 있는 자리에 마커를 놓는다 (STATUS.md 의 1차-B 경계 상자)
    z0 = -2.31                                    # 바닥 평면
    spots = [(-12.0, -13.5), (0.8, -13.5), (-12.0, -0.6), (0.8, -0.6), (-5.6, -7.0)]
    scale_odm_per_m = 14.87 / 1.20                # 매트 실측 1.2m ↔ 복원 14.87 단위
    size_odm = 0.10 * scale_odm_per_m

    # 되찾아야 할 정답 변환 (ODM → 실측). 임의로 정한다.
    s_true = 1.0 / scale_odm_per_m
    R_true, _ = cv2.Rodrigues(np.array([0.04, -0.02, 0.9]))
    t_true = np.array([0.75, 0.62, 0.013])

    src, dst = [], []
    nobs = []
    for mid, (x, y) in enumerate(spots):
        centre = np.array([x, y, z0 + (1.0 if mid == 4 else 0.0)])   # 하나는 높이 다르게
        corners_odm = align.world_corners(centre, size_odm, "+Y")
        for k, X in enumerate(corners_odm):
            obs = []
            for n, (R, C) in shots.items():
                uv, z = cam.project(X, R, C)
                if z[0] <= 0:
                    continue
                if not (0 <= uv[0, 0] < cam.width and 0 <= uv[0, 1] < cam.height):
                    continue
                if (R @ np.array([0, 0, 1.0]))[2] > 0:        # 마커 뒷면이면 안 보인다
                    continue
                obs.append((R, C, cam.undistort(uv[0] + rng.normal(0, 1.0, 2))[0]))
            if len(obs) < 3:
                continue
            nobs.append(len(obs))
            src.append(align.triangulate(obs))                # ODM 좌표계 복원값
            dst.append(align.apply(s_true, R_true, t_true, X)[0])   # 자로 잰 값

    src, dst = np.array(src), np.array(dst)
    check("코너 관측 확보", len(src) >= 12,
          f"{len(src)}/20 개, 코너당 평균 {np.mean(nobs):.0f}장")

    s, R, t = align.umeyama(src, dst)
    res = np.linalg.norm(align.apply(s, R, t, src) - dst, axis=1)
    ang = np.degrees(np.linalg.norm(cv2.Rodrigues(R.T @ R_true)[0]))

    check("축척 오차 < 0.5%", abs(s - s_true) / s_true < 0.005,
          f"{abs(s - s_true) / s_true * 100:.3f}%")
    check("회전 오차 < 0.3도", ang < 0.3, f"{ang:.4f}도")
    check("잔차 중앙 < 3mm", np.median(res) < 0.003,
          f"중앙 {np.median(res)*1000:.2f}mm / 최대 {res.max()*1000:.2f}mm")

    # 독립 검증 — 마커 사이 실측 거리가 복원에서도 맞는가
    cen_src = src.reshape(-1, 4, 3).mean(1)
    cen_dst = dst.reshape(-1, 4, 3).mean(1)
    d_rec = [np.linalg.norm(align.apply(s, R, t, cen_src[i])[0]
                            - align.apply(s, R, t, cen_src[j])[0])
             for i in range(len(cen_src)) for j in range(i + 1, len(cen_src))]
    d_true = [np.linalg.norm(cen_dst[i] - cen_dst[j])
              for i in range(len(cen_dst)) for j in range(i + 1, len(cen_dst))]
    e = np.abs(np.array(d_rec) - np.array(d_true))
    check("마커 간 거리 오차 < 3mm", e.max() < 0.003,
          f"최대 {e.max()*1000:.2f}mm ({len(e)}쌍)")


def main() -> None:
    cam, shots, rec = load()
    test_pose_convention(shots, rec)
    test_projection(cam, shots, rec)
    test_projection_vs_images(cam, shots, rec)
    test_triangulation(cam, shots, rec)
    test_umeyama()
    test_world_corners()
    test_end_to_end(cam, shots, rec)
    n, ok = len(_results), sum(_results)
    print(f"\n{'='*60}\n{ok}/{n} 통과" + ("" if ok == n else f"  — {n-ok}건 실패"))
    raise SystemExit(0 if ok == n else 1)


if __name__ == "__main__":
    main()
