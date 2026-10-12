"""사진 폴더 → 마커 배치표 (markers_W.json). 자로 재지 않는다 — 인쇄된 마커 한 변만 잰다.

    uv run python marker_map.py ../aerover/sessions/<회차> --size 0.100
    uv run python marker_map.py <폴더> --size 0.100 --ref 0 2 1.062      # 축척을 긴 실측 거리로 (권장)
    uv run python marker_map.py <폴더> --size 0.06 --init markers_W.json # 이전 배치에서 출발 (다시 붙였을 때)

흐름:

    장마다 마커 검출 — 2개 이상 보인 장만 쓴다 (마커끼리를 잇는 것은 "같이 찍힌 장" 이다)
      → 초기값: 마커를 하나씩 풀어(IPPE_SQUARE, 두 해 중 그 장의 판 법선에 맞는 쪽) 같이 찍힌 마커끼리의
               상대 자세를 사슬로 잇는다
      → 번들 조정: 모든 마커의 자세 + 모든 장의 카메라 자세를 한꺼번에, 코너 재투영 오차 최소 (Huber)
      → W 로 옮긴다 (아래)

## 축척은 마커 한 변이 정한다

사진만으로는 크기를 모른다. 마커 코너를 `--size` 크기의 정사각형으로 두는 것이 유일한 축척 정보라,
**한 변 실측 오차가 그대로 비율 오차**가 된다 (0.5mm / 100mm = 0.5% → 1.2m 판에서 6mm).
인쇄 후 자로 한 번 재서 준다.

**실제로는 마커 한 변만으로는 축척이 3% 남짓 커졌다** (2026-09-22 시연 판, 두 회차 모두 +2.9 · +3.4%).
10cm 마커가 1.2m 판 안에 있으면 양 끝 모서리 마커의 중심 거리는 1.10m 를 넘을 수 없는데 1.13m 가 나왔다.
코너 정밀화 방식(±1%)이나 카메라 값(번들조정값도 +3.7%)은 원인이 아니었고, 판 위에 종이만 얹어
마커가 휘거나 들뜬 것(Z 가 ±1cm 흩어짐)이 유력하다. 휜 마커는 작게 투영돼 배치가 커진다.
→ **`--ref A B 거리` 로 두 마커 중심 사이를 줄자로 잰 값을 주면 그 값으로 축척을 다시 맞춘다.**
긴 거리일수록 좋다 (1.1m 를 ±2mm 로 재면 0.2%, 마커 한 변 0.5mm 는 0.5%).

같은 이유로 **코너가 한쪽으로 치우치면 축척이 틀린다.** 기본 SUBPIX 정밀화는 코너를 안쪽으로 0.35px
당겨서(64px 마커) 배치가 0.74% 커졌다. 그래서 여기서는 치우침이 없는 APRILTAG 정밀화를 쓴다 —
장마다 흔들림은 더 크지만 수십 장을 함께 풀면 흔들림은 평균되고 치우침만 남기 때문이다.

## W 를 어떻게 잡나

3D 맵과 실시간 영상이 같은 마커로 맞물리기만 하면 원점은 어디든 된다. 그래도 준비작업 문서의 W
(원점 판 앞-왼 모서리, +X 앞 변, +Y 뒤, +Z 위)에 가깝게 잡는다 — 배경 제거 상자를 판 크기로 쓰기 위해서다.

    Z   마커 면에 맞춘 평면의 법선, 카메라 쪽이 +. 마커 표면이 Z=0
    X   마커 중심들을 감싸는 최소 사각형의 한 변. 네 방향 중 **ID 가 가장 작은 마커가 앞-왼쪽**에 오는 쪽
    원점  마커 중심들의 최소 x · y 가 `--inset` (기본 0.07 = 문서의 판 모서리 → 마커 중심 거리) 이 되는 곳

마커를 문서 표대로(ID 0 이 앞-왼 모서리, 모서리에서 7cm) 붙였으면 W 가 판 모서리 좌표계와 거의 같다.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation

import pose as P

Frame = dict[int, np.ndarray]          # {ID: 픽셀 코너 (4,2)}
MIN_SEEN = 3                           # 이보다 적은 장에 찍힌 마커는 결과에 경고를 단다
# 판에 붙인 마커 (ArUco_준비작업.md 3절). 10 은 비워 둔다 — 09-29 에 판에 없는 10 · 17 이 오검출됐다
BOARD_IDS = frozenset([*range(10), 11, 12])


def local_corners(size: float) -> np.ndarray:
    """마커 자기 좌표계의 코너 (4,3). 위쪽이 +y, cv2 검출 순서(좌상·우상·우하·좌하)와 같다."""
    h = size / 2
    return np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]])


def to_T(rvec, tvec) -> np.ndarray:
    T = np.eye(4); T[:3, :3] = cv2.Rodrigues(np.asarray(rvec, float))[0]; T[:3, 3] = np.ravel(tvec)
    return T


def from_T(T: np.ndarray) -> np.ndarray:
    return np.r_[cv2.Rodrigues(T[:3, :3])[0].ravel(), T[:3, 3]]


def detect_folder(folder: Path, keep: frozenset[int] = BOARD_IDS) -> tuple[list[str], list[Frame]]:
    """`keep` 의 ID 만 받는다. 판에 없는 ID 는 오검출이다 — 한 장에만 찍혀 엉뚱한 자리에 풀리고,
    원점(마커 중심 최소 x · y)을 끌고 가서 배치 전체를 밀어 버린다 (2026-09-29 회차 180936 의 ID 17)."""
    # 축척이 마커 한 변에서만 나오므로 코너 치우침이 없는 APRILTAG 정밀화를 쓴다.
    # SUBPIX 는 코너를 안쪽으로 0.35px 당겨 배치가 0.7% 커졌다 (test_marker_map.py)
    det = P.make_detector(cv2.aruco.CORNER_REFINE_APRILTAG)
    names, frames = [], []
    for f in sorted(folder.glob("*.jpg")):
        corners, ids, _ = det.detectMarkers(cv2.imread(str(f), cv2.IMREAD_GRAYSCALE))
        if ids is None:
            continue
        fr = {int(i): c.reshape(4, 2).astype(float) for c, i in zip(corners, ids.ravel()) if int(i) in keep}
        if len(fr) >= 2:
            names.append(f.name)
            frames.append(fr)
    return names, frames


def _medoid(Ts: list[np.ndarray]) -> np.ndarray:
    """후보 자세 중 다른 후보들과 가장 가까운 것. 평균을 내면 튀는 후보 하나에 끌려간다."""
    if len(Ts) <= 2:
        return Ts[0]
    t = np.array([T[:3, 3] for T in Ts])
    cost = np.linalg.norm(t[:, None] - t[None], axis=2).sum(1)
    return Ts[int(np.argmin(cost))]


def single_poses(fr: Frame, obj: np.ndarray, K, dist) -> dict[int, np.ndarray]:
    """한 장의 마커를 하나씩 푼 자세 {ID: T_cam←marker}.

    작은 마커를 비스듬히 보면 평면 뒤집힘 모호성으로 IPPE 의 두 해가 재투영으로는 거의 구별되지 않는다.
    첫 번째 해만 쓰면 틀린 쪽이 자주 뽑혀 초기값이 엉뚱한 곳에 갇혔다 (2026-09-29, 45px 안팎 마커:
    재투영 중앙 3px · 최대 20px 대 → 이 선택으로 0.7px). 한 장의 마커는 모두 같은 판 위에 있으니
    **법선이 가장 많이 모이는 방향**을 그 장의 판 법선으로 보고, 마커마다 그 법선에 가까운 해를 고른다.
    """
    cands: dict[int, list[np.ndarray]] = {}
    for i, c in fr.items():
        n, rvs, tvs, _ = cv2.solvePnPGeneric(obj, c, K, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
        if n:
            cands[i] = [to_T(rv, tv) for rv, tv in zip(rvs, tvs)]
    normals = [T[:3, 2] for Ts in cands.values() for T in Ts]
    if not normals:
        return {}
    # 후보 법선마다 "20° 안에 드는 해를 가진 마커 수" — 가장 많은 것이 판 법선
    cos20 = np.cos(np.radians(20))
    votes = [sum(any(T[:3, 2] @ nrm > cos20 for T in Ts) for Ts in cands.values()) for nrm in normals]
    plane_n = normals[int(np.argmax(votes))]
    return {i: max(Ts, key=lambda T: T[:3, 2] @ plane_n) for i, Ts in cands.items()}


def initial_guess(frames: list[Frame], K, dist, size: float):
    """마커 자세(기준 마커 좌표계)와 장별 카메라 자세의 초기값."""
    obj = local_corners(size)
    single = [single_poses(fr, obj, K, dist) for fr in frames]    # 장마다 {ID: T_cam←marker}

    seen = {i: sum(i in fr for fr in frames) for fr in frames for i in fr}
    ref = max(seen, key=lambda i: (seen[i], -i))         # 가장 많이 찍힌 마커를 기준으로
    marker = _grow({ref: np.eye(4)}, single)             # T_ref←marker
    return ref, marker, _initial_cams(frames, marker, obj, K, dist)


def _grow(marker: dict[int, np.ndarray], single: list[dict[int, np.ndarray]]) -> dict[int, np.ndarray]:
    """아는 마커와 한 장에 같이 찍힌 마커의 자세 T_ref←marker 를 더해 간다 (더 늘지 않을 때까지). 새 dict 를 돌려준다."""
    marker = dict(marker)
    while True:
        cand: dict[int, list[np.ndarray]] = {}
        for s in single:
            known = [k for k in s if k in marker]
            if not known:
                continue
            k = known[0]
            T_ref_cam = marker[k] @ np.linalg.inv(s[k])
            for m in s:
                if m not in marker:
                    cand.setdefault(m, []).append(T_ref_cam @ s[m])
        if not cand:
            return marker
        for m, Ts in cand.items():
            marker[m] = _medoid(Ts)


def _initial_cams(frames: list[Frame], marker: dict[int, np.ndarray], obj: np.ndarray, K, dist) -> list[np.ndarray]:
    """장마다 T_cam←ref — 그 장에 보인 마커 전부로 한 번에 푼다."""
    cams = []
    for fr in frames:
        ids = [i for i in fr if i in marker]
        X = np.vstack([(marker[i] @ np.c_[obj, np.ones(4)].T).T[:, :3] for i in ids])
        x = np.vstack([fr[i] for i in ids])
        ok, rv, tv = cv2.solvePnP(X, x, K, dist, flags=cv2.SOLVEPNP_SQPNP)
        cams.append(to_T(rv, tv))
    return cams


def guess_from_layout(frames: list[Frame], K, dist, size: float, layout: dict[int, np.ndarray]):
    """이전 배치표(또는 자로 잰 배치)에서 출발하는 초기값. `initial_guess` 와 같은 형태를 돌려준다.

    사진만으로 푼 초기값은 마커가 작고(40px 안팎) 한 장에 2개씩만 이어지는 회차에서 아직 가끔 갇힌다
    (2026-09-29 회차 220336: 재투영 중앙 4.9px · 최대 100px). 마커를 조금 옮겨 다시 붙인 정도(1cm 안팎)면
    이전 배치표가 충분히 가까운 출발점이라 번들 조정이 제대로 수렴한다 (같은 회차 0.67px).
    배치표에 없는 마커(새로 붙인 것)는 `_triangulate_new` 로 더한다.
    """
    obj = local_corners(size)
    T_w = {i: _fit_rigid(obj, layout[i]) for i in layout}                # T_W←marker
    seen = {i: sum(i in fr for fr in frames) for fr in frames for i in fr if i in layout}
    ref = max(seen, key=lambda i: (seen[i], -i))
    marker = {i: np.linalg.inv(T_w[ref]) @ T_w[i] for i in seen}         # T_ref←marker
    marker = _triangulate_new(frames, marker, obj, K, dist)
    return ref, marker, _initial_cams(frames, marker, obj, K, dist)


def _triangulate_new(frames: list[Frame], marker: dict[int, np.ndarray], obj: np.ndarray, K, dist) -> dict[int, np.ndarray]:
    """`marker` 에 없는 마커를 더한 새 dict. 아는 마커만으로 자세를 푼 장들에서 코너를 삼각측량한다 (2장 이상).

    한 장씩 푼 단독 자세(`_grow`)로 이으면 작은 마커의 뒤집힌 해가 섞여 번들 조정이 갇혔다
    (2026-10-03 회차 184426, 11 · 12 추가: 재투영 중앙 2.7px — 같은 장에서 0~9 만이면 0.68px).
    """
    new = sorted({i for fr in frames for i in fr} - set(marker))
    if not new:
        return marker
    views: dict[int, list[tuple[np.ndarray, np.ndarray]]] = {i: [] for i in new}   # (투영행렬, 왜곡 편 코너)
    for fr in frames:
        ids = [i for i in fr if i in marker]
        if len(ids) < 2 or not set(fr) & set(new):
            continue
        X = np.vstack([(marker[i] @ np.c_[obj, np.ones(4)].T).T[:, :3] for i in ids])
        ok, rv, tv = cv2.solvePnP(X, np.vstack([fr[i] for i in ids]), K, dist, flags=cv2.SOLVEPNP_SQPNP)
        Pm = K @ to_T(rv, tv)[:3]
        for i in set(fr) & set(new):
            views[i].append((Pm, cv2.undistortPoints(fr[i].reshape(-1, 1, 2), K, dist, P=K).reshape(4, 2)))
    out = dict(marker)
    for i, v in views.items():
        if len(v) < 2:
            continue                                     # 못 풀면 빠진다 — 결과 표에 안 나온다
        X = []
        for c in range(4):                               # 코너마다 DLT
            A = np.vstack([np.vstack([u[c, 0] * Pm[2] - Pm[0], u[c, 1] * Pm[2] - Pm[1]]) for Pm, u in v])
            h = np.linalg.svd(A)[2][-1]
            X.append(h[:3] / h[3])
        out[i] = _fit_rigid(obj, np.array(X))
    return out


def _fit_rigid(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """A 를 B 에 맞추는 강체 변환 4x4 (Kabsch, 축척 없음). 배치표 코너는 한 변이 조금 달라도 된다."""
    ca, cb = A.mean(0), B.mean(0)
    U, _, Vt = np.linalg.svd((A - ca).T @ (B - cb))
    D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    T = np.eye(4); T[:3, :3] = Vt.T @ D @ U.T; T[:3, 3] = cb - T[:3, :3] @ ca
    return T


def bundle_adjust(frames: list[Frame], K, dist, size: float, ref: int,
                  marker: dict[int, np.ndarray], cams: list[np.ndarray]):
    """마커 자세 + 카메라 자세를 한꺼번에. 기준 마커는 고정한다 (전체를 옮겨도 같은 해라서)."""
    obj = local_corners(size)
    mids = sorted(i for i in marker if i != ref)
    mcol = {m: 6 * k for k, m in enumerate(mids)}
    base = 6 * len(mids)
    x0 = np.r_[[from_T(marker[m]) for m in mids] if mids else [], [from_T(T) for T in cams]].ravel()
    pairs = [(f, i) for f, fr in enumerate(frames) for i in fr if i in marker]

    def unpack(x):
        M = {ref: np.eye(4), **{m: to_T(x[mcol[m]:mcol[m] + 3], x[mcol[m] + 3:mcol[m] + 6]) for m in mids}}
        return M, x[base:].reshape(-1, 6)

    # 잔차는 최적화 중 수백 번 불린다. 쌍마다 cv2.projectPoints 를 부르면 3분 걸려서(실측)
    # 모든 쌍을 numpy 로 한 번에 투영한다 — 수식은 OpenCV 와 같다 (k1 k2 p1 p2 k3)
    pf = np.array([f for f, _ in pairs]); pm = [i for _, i in pairs]
    obs = np.stack([frames[f][i] for f, i in pairs])                     # (P,4,2)
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    k1, k2, p1, p2, k3 = np.ravel(dist)[:5]

    def residual(x):
        mp = np.array([np.zeros(6) if i == ref else x[mcol[i]:mcol[i] + 6] for i in pm])   # (P,6)
        cp = x[base:].reshape(-1, 6)[pf]                                                  # (P,6)
        Rm = Rotation.from_rotvec(mp[:, :3]).as_matrix()                                  # (P,3,3)
        Xw = np.einsum("pij,kj->pki", Rm, obj) + mp[:, None, 3:]                         # (P,4,3)
        Rc = Rotation.from_rotvec(cp[:, :3]).as_matrix()
        Xc = np.einsum("pij,pkj->pki", Rc, Xw) + cp[:, None, 3:]
        xn, yn = Xc[..., 0] / Xc[..., 2], Xc[..., 1] / Xc[..., 2]
        r2 = xn * xn + yn * yn
        rad = 1 + k1 * r2 + k2 * r2 ** 2 + k3 * r2 ** 3
        xd = xn * rad + 2 * p1 * xn * yn + p2 * (r2 + 2 * xn * xn)
        yd = yn * rad + p1 * (r2 + 2 * yn * yn) + 2 * p2 * xn * yn
        uv = np.stack([fx * xd + cx, fy * yd + cy], -1)
        return (uv - obs).ravel()

    # 코너 8개 잔차는 그 마커 6개 + 그 장의 카메라 6개에만 걸린다 — 희소 구조를 알려주면 빨라진다
    S = lil_matrix((8 * len(pairs), len(x0)), dtype=int)
    for r, (f, i) in enumerate(pairs):
        S[8 * r:8 * r + 8, base + 6 * f:base + 6 * f + 6] = 1
        if i != ref:
            S[8 * r:8 * r + 8, mcol[i]:mcol[i] + 6] = 1
    sol = least_squares(residual, x0, jac_sparsity=S, loss="huber", f_scale=1.0, x_scale="jac")
    M, C = unpack(sol.x)
    res = residual(sol.x).reshape(-1, 4, 2)
    err = {p: float(np.sqrt(np.mean(np.sum(r ** 2, 1)))) for p, r in zip(pairs, res)}
    corners = {i: (M[i][:3, :3] @ obj.T).T + M[i][:3, 3] for i in M}
    cam_centers = np.array([-to_T(c[:3], c[3:])[:3, :3].T @ c[3:] for c in C])
    return corners, cam_centers, err


def to_world(corners: dict[int, np.ndarray], cam_centers: np.ndarray, inset: float) -> dict[int, np.ndarray]:
    """기준 마커 좌표계 → W. 규칙은 모듈 설명 'W 를 어떻게 잡나'."""
    pts = np.vstack(list(corners.values()))
    o = pts.mean(0)
    n = np.linalg.svd(pts - o)[2][2]
    if np.mean((cam_centers - o) @ n) < 0:
        n = -n
    e1 = np.cross(n, [0, 0, 1.0]) if abs(n[2]) < 0.9 else np.cross(n, [1.0, 0, 0])
    e1 /= np.linalg.norm(e1); e2 = np.cross(n, e1)
    ids = sorted(corners)
    c2 = np.array([[(corners[i].mean(0) - o) @ e1, (corners[i].mean(0) - o) @ e2] for i in ids])
    ang = np.radians(cv2.minAreaRect(c2.astype(np.float32))[2])
    u = np.array([np.cos(ang), np.sin(ang)])
    first = c2[0]                                        # ID 가 가장 작은 마커
    best = None
    for X2 in (u, -u, np.array([-u[1], u[0]]), np.array([u[1], -u[0]])):
        Y2 = np.array([-X2[1], X2[0]])                   # Z 기준 반시계 90° — 오른손 좌표계
        score = first @ X2 + first @ Y2                  # 앞-왼쪽일수록 작다
        if best is None or score < best[0]:
            best = (score, X2, Y2)
    _, X2, Y2 = best
    X3, Y3 = X2[0] * e1 + X2[1] * e2, Y2[0] * e1 + Y2[1] * e2
    B = np.stack([X3, Y3, n])                            # 행이 W 의 축
    W = {i: (B @ (corners[i] - o).T).T for i in ids}
    cxy = np.array([W[i].mean(0)[:2] for i in ids])
    shift = np.r_[inset - cxy.min(0), 0.0]
    return {i: W[i] + shift for i in ids}


def main() -> None:
    ap = argparse.ArgumentParser(description="사진 폴더 → 마커 배치표 (자로 재지 않는다)")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--size", type=float, required=True, help="인쇄된 마커 한 변 실측값 (m)")
    ap.add_argument("--inset", type=float, default=0.07, help="판 모서리 → 마커 중심 거리 (m)")
    ap.add_argument("--ref", nargs=3, metavar=("A", "B", "M"),
                    help="마커 A · B 중심 사이 줄자 실측 거리(m) — 이 값으로 축척을 맞춘다")
    ap.add_argument("--ids", type=int, nargs="+", default=sorted(BOARD_IDS),
                    help="판에 붙인 마커 ID (나머지는 오검출로 버린다). 기본: BOARD_IDS")
    ap.add_argument("--init", type=Path, help="초기값으로 쓸 이전 배치표 (마커를 다시 붙였을 때 등). 없으면 사진만으로")
    ap.add_argument("--camera", type=Path, default=P.CAMERA_FILE)
    ap.add_argument("--out", type=Path, default=Path("markers_W.json"))
    a = ap.parse_args()

    K, dist = P.load_camera(a.camera)
    names, frames = detect_folder(a.folder, frozenset(a.ids))
    if len(frames) < 3:
        raise SystemExit(f"마커가 2개 이상 보인 장이 {len(frames)}장뿐이다 — 한 바퀴 돌며 더 찍는다")
    if a.init:
        ref, marker, cams = guess_from_layout(frames, K, dist, a.size, P.load_layout(a.init))
    else:
        ref, marker, cams = initial_guess(frames, K, dist, a.size)
    corners, cam_centers, err = bundle_adjust(frames, K, dist, a.size, ref, marker, cams)
    W = to_world(corners, cam_centers, a.inset)
    scale_note = f"마커 한 변 {a.size} m"
    if a.ref:
        i, j, dist_m = int(a.ref[0]), int(a.ref[1]), float(a.ref[2])
        got = np.linalg.norm(W[i].mean(0) - W[j].mean(0))
        k = dist_m / got
        print(f"축척 보정: 마커 {i}↔{j} 사진 {got:.4f} m → 실측 {dist_m:.4f} m ({(k - 1) * 100:+.2f}%)")
        W = to_world({m: c * k for m, c in W.items()}, cam_centers * k, a.inset)
        scale_note = f"마커 {i}↔{j} 실측 {dist_m} m (사진값 대비 {(k - 1) * 100:+.2f}%)"

    seen = {i: sum(i in fr for fr in frames) for i in W}
    allerr = np.array(list(err.values()))
    print(f"\n{len(frames)}장 사용 (마커 2개 이상), 마커 {len(W)}개, 기준 ID {ref}")
    print(f"코너 재투영 RMS: 전체 중앙 {np.median(allerr):.2f} px · 최대 {allerr.max():.2f} px")
    print("\n ID   중심 X      Y       Z (m)    찍힌 장   재투영 중앙")
    for i in sorted(W):
        c = W[i].mean(0)
        e = np.median([v for (f, m), v in err.items() if m == i])
        warn = "  ⚠ 장수 부족 — 위치를 믿기 어렵다" if seen[i] < MIN_SEEN else ""
        print(f" {i:2d}  {c[0]:7.3f} {c[1]:7.3f} {c[2]:7.3f}     {seen[i]:3d}      {e:.2f} px{warn}")
    lost = sorted({i for fr in frames for i in fr} - set(W))
    if lost:
        print(f"\n⚠ 다른 마커와 같이 찍힌 장이 없어 배치를 못 구한 ID: {lost}")

    doc = {"note": f"marker_map.py 가 사진으로 잰 배치 ({date.today().isoformat()}, {a.folder.name}, "
                   f"{len(frames)}장). 마커 표면이 Z=0. 축척: {scale_note}",
           "size_m": a.size,
           "markers": {str(i): {"corners": np.round(W[i], 5).tolist(), "seen": seen[i]} for i in sorted(W)},
           "stats": {"frames": len(frames), "reproj_median_px": round(float(np.median(allerr)), 3),
                     "reproj_max_px": round(float(allerr.max()), 3), "ref_id": ref}}
    a.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print(f"\n저장: {a.out}")


if __name__ == "__main__":
    main()
