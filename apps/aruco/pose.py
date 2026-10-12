"""실시간 자세 추정 — 한 프레임의 ArUco 마커 → 드론 카메라 자세 (시연 좌표계 W).

    uv run python pose.py <사진 또는 폴더>                 # 장마다 카메라 위치 출력
    uv run python pose.py <폴더> --draw <출력폴더>         # W 축 · 검출 마커를 그린 사본

흐름:

    프레임 → ArUco 검출 (서브픽셀 코너)
      → 배치표(markers_W.json)에 있는 ID 만 골라 코너 4개씩 W 좌표와 짝짓는다
      → **보이는 마커 전부를 한 번의 solvePnP 에** (SQPNP → LM 다듬기)
      → R (W→카메라 회전), C (W 에서의 카메라 위치), 재투영 오차

마커를 하나씩 풀어 평균내지 않는다. 마커 하나는 화면에서 작아 각도가 튀고
(평면 모호성), 여러 마커의 코너를 한 식에 넣으면 서로 멀리 떨어진 점들이
각도를 붙잡아 준다. 갠트리 궤도에서는 늘 4개 이상 보인다 (README 가시성 표).

규약 (align.py 와 같다):

    Xc = R · (Xw - C)
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from align import ARUCO_DICT, world_corners

HERE = Path(__file__).parent
CAMERA_FILE = HERE / "camera_1280x960.json"
LAYOUT_FILE = HERE / "markers_W.json"


@dataclass
class Pose:
    R: np.ndarray           # W → 카메라 회전 (3,3)
    C: np.ndarray           # W 에서의 카메라 위치 (3,) [m]
    ids: list[int]          # 풀이에 쓴 마커
    err_px: float           # 재투영 RMS

    @property
    def rvec_tvec(self) -> tuple[np.ndarray, np.ndarray]:
        """OpenCV 함수(projectPoints · drawFrameAxes)가 받는 형태."""
        return cv2.Rodrigues(self.R)[0], (-self.R @ self.C).reshape(3, 1)


def load_camera(path: Path = CAMERA_FILE) -> tuple[np.ndarray, np.ndarray]:
    d = json.loads(Path(path).read_text())
    return np.array(d["K"]), np.array(d["dist"])


def load_layout(path: Path = LAYOUT_FILE) -> dict[int, np.ndarray]:
    """배치표 → {ID: 코너 4개의 W 좌표 (4,3)}. 코너 순서는 cv2 검출과 같다.

    마커마다 두 형식 중 하나다:
      {"corners": [[x,y,z] x4]}          marker_map.py 가 사진으로 잰 것 — 방향 자유
      {"center": [x,y,z], "up": "+Y"}    자로 잰 것 — 판에 평평하게, 변을 축과 나란히
    """
    d = json.loads(Path(path).read_text())
    return {int(k): np.array(m["corners"], float) if "corners" in m
            else world_corners(np.array(m["center"]), d["size_m"], m["up"])
            for k, m in d["markers"].items()}


def make_detector(refine: int = cv2.aruco.CORNER_REFINE_SUBPIX) -> cv2.aruco.ArucoDetector:
    """코너 정밀화 방식에 따라 성격이 다르다 (64px 마커 합성 시험, marker_map.py 참고):

        SUBPIX    흔들림 작음 (중앙 0.43px) · 코너가 **안쪽으로 0.35px 치우침**
        APRILTAG  흔들림 큼   (중앙 0.68px) · 치우침 거의 없음 (0.01px)

    실시간 자세는 배치 전체가 축척을 잡아 치우침이 묻힌다 → SUBPIX (기본).
    marker_map 은 축척이 마커 한 변에서만 나와 치우침이 그대로 축척이 된다 → APRILTAG.
    """
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = refine       # 기본값(NONE)은 코너를 정수 픽셀 근처에서 멈춘다
    # 비트 해독 때 칸당 픽셀 (기본 4). 멀고 비스듬한 마커가 해독에서 떨어지던 것이 붙는다 —
    # 손 촬영 225043 40장: 프레임당 마커 평균 2.8 → 3.7, 속도 같음 (2026-09-22)
    params.perspectiveRemovePixelPerCell = 8
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(ARUCO_DICT), params)


class PoseEstimator:
    def __init__(self, K: np.ndarray, dist: np.ndarray, layout: dict[int, np.ndarray]) -> None:
        self.K, self.dist, self.layout = K, dist, layout
        self.detector = make_detector()

    def detect(self, gray: np.ndarray) -> dict[int, np.ndarray]:
        """{ID: 픽셀 코너 (4,2)}. 배치표에 없는 ID(오검출 · 다른 마커)는 버린다."""
        corners, ids, _ = self.detector.detectMarkers(gray)
        if ids is None:
            return {}
        return {int(i): c.reshape(4, 2) for c, i in zip(corners, ids.ravel()) if int(i) in self.layout}

    def estimate(self, seen: dict[int, np.ndarray]) -> Pose | None:
        if not seen:
            return None
        ids = sorted(seen)
        obj = np.vstack([self.layout[i] for i in ids]).astype(np.float64)
        img = np.vstack([seen[i] for i in ids]).astype(np.float64)
        # SQPNP 는 전역해를 찾는다 (초기값 불필요, 평면 배치도 된다). LM 으로 재투영 오차를 마저 줄인다
        ok, rvec, tvec = cv2.solvePnP(obj, img, self.K, self.dist, flags=cv2.SOLVEPNP_SQPNP)
        if not ok:
            return None
        rvec, tvec = cv2.solvePnPRefineLM(obj, img, self.K, self.dist, rvec, tvec)
        proj, _ = cv2.projectPoints(obj, rvec, tvec, self.K, self.dist)
        err = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img) ** 2, 1))))
        R = cv2.Rodrigues(rvec)[0]
        return Pose(R, (-R.T @ tvec).ravel(), ids, err)

    def __call__(self, gray: np.ndarray) -> Pose | None:
        return self.estimate(self.detect(gray))


def draw(img: np.ndarray, est: PoseEstimator, pose: Pose | None, seen: dict[int, np.ndarray]) -> np.ndarray:
    """검출 마커 테두리 + W 원점 축(빨강 X · 초록 Y · 파랑 Z, 길이 0.2m)."""
    out = img.copy()
    if seen:
        cv2.aruco.drawDetectedMarkers(out, [c.reshape(1, 4, 2).astype(np.float32) for c in seen.values()],
                                      np.array(list(seen), dtype=np.int32).reshape(-1, 1))
    if pose is not None:
        rvec, tvec = pose.rvec_tvec
        cv2.drawFrameAxes(out, est.K, est.dist, rvec, tvec, 0.2, 3)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="사진 → 드론 카메라 자세 (시연 좌표계 W)")
    ap.add_argument("path", type=Path, help="사진 한 장 또는 폴더")
    ap.add_argument("--camera", type=Path, default=CAMERA_FILE)
    ap.add_argument("--layout", type=Path, default=LAYOUT_FILE)
    ap.add_argument("--draw", type=Path, help="축 · 마커를 그린 사본을 저장할 폴더")
    a = ap.parse_args()

    est = PoseEstimator(*load_camera(a.camera), load_layout(a.layout))
    files = sorted(a.path.glob("*.jpg")) if a.path.is_dir() else [a.path]
    if a.draw:
        a.draw.mkdir(parents=True, exist_ok=True)
    center = np.vstack(list(est.layout.values())).mean(0)      # 마커들의 가운데 ≈ 판 중심
    counts, errs = [], []
    for f in files:
        img = cv2.imread(str(f))
        seen = est.detect(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        pose = est.estimate(seen)
        counts.append(len(seen))
        if pose is None:
            print(f"{f.name}  마커 없음")
        else:
            errs.append(pose.err_px)
            x, y, z = pose.C
            print(f"{f.name}  마커 {pose.ids}  C = ({x:.3f}, {y:.3f}, {z:.3f}) m  "
                  f"판 중심까지 {np.linalg.norm(pose.C - center):.3f} m  재투영 {pose.err_px:.2f} px")
        if a.draw:
            cv2.imwrite(str(a.draw / f.name), draw(img, est, pose, seen))

    # 마커 크기를 줄여가며 비교할 때 보는 줄. 갠트리에서 "2개 미만" 이 생기기 시작하면 그 크기가 한계다
    if len(files) > 1:
        c = np.array(counts)
        print(f"\n요약 {len(c)}장 — 프레임당 마커 최소 {c.min()} · 평균 {c.mean():.1f} · "
              f"2개 미만 {np.mean(c < 2) * 100:.0f}% · 자세 실패 {np.sum(c == 0)}장"
              + (f" · 재투영 중앙 {np.median(errs):.2f} px" if errs else ""))


if __name__ == "__main__":
    main()
