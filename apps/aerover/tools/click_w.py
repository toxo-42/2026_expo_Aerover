"""사진을 클릭하면 그 점의 시연 좌표계 W (x, y, z) 를 보여준다 — 배치표 · 정합이 맞는지 눈으로 확인하려고.

    uv run python -m tools.click_w sessions/20260929_220336
    uv run python -m tools.click_w sessions/20260929_220336 --model 3D_model/20260929_194931

사진마다 보이는 마커로 카메라 자세를 풀고(`wframe` 의 자세 추정기 — 앱과 같은 것), 클릭한 픽셀의 광선을
    --model 을 주면   그 회차의 메시(to_W.json 으로 W 로 옮긴 것)와 교차 — 탐지 페이지의 핀과 같은 계산
    안 주면           판 윗면 Z=0 평면과 교차 — 판 위 점(마커 모서리 · 판 가장자리)을 자로 잰 값과 대 보기 좋다
시킨다. 계산은 전부 `src.core.wframe` · aruco `backproject` 것을 그대로 쓴다.

    왼쪽 클릭   점 찍기 (터미널에도 출력)
    n · 스페이스  다음 장      p  이전 장      c  점 지우기      q · Esc  끝
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import use_utf8_stdout     # noqa: E402
from src.core import wframe           # noqa: E402  (aruco 경로도 여기서 잡힌다)
import backproject as bp              # noqa: E402  (aruco)

WIN = "click -> W"
DOT, MARK, BAD, TEXT = (0, 220, 255), (80, 200, 80), (60, 60, 230), (255, 255, 255)


@dataclass
class Picked:
    uv: tuple[int, int]
    xyz: np.ndarray | None           # 메시에 안 맞으면 None


def floor_point(uv: np.ndarray, pose: wframe.Pose, K, dist) -> np.ndarray | None:
    """픽셀 광선 ∩ Z=0 평면. 광선이 위를 향하면 None."""
    d = bp.pixel_rays(uv.reshape(1, 2), pose, K, dist)[0]
    if d[2] >= 0:
        return None
    return pose.C + d * (-pose.C[2] / d[2])


def pick(uv: tuple[int, int], pose: wframe.Pose, est: wframe.PoseEstimator,
         mesh: wframe.Mesh | None) -> Picked:
    p = np.array(uv, float)
    if mesh is None:
        return Picked(uv, floor_point(p, pose, est.K, est.dist))
    xyz = bp.backproject(p.reshape(1, 2), pose, est.K, est.dist, mesh)[0]
    return Picked(uv, None if np.isnan(xyz).any() else xyz)


def render(img: np.ndarray, seen: dict, pose, picks: list[Picked], title: str) -> np.ndarray:
    out = img.copy()
    for i, c in seen.items():
        cv2.polylines(out, [c.astype(np.int32)], True, MARK, 2)
        cv2.putText(out, str(i), tuple(c[0].astype(int)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, MARK, 2)
    for p in picks:
        cv2.circle(out, p.uv, 5, DOT, -1)
        label = "miss" if p.xyz is None else "({:.1f}, {:.1f}, {:.1f})cm".format(*(p.xyz * 100))
        cv2.putText(out, label, (p.uv[0] + 8, p.uv[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, DOT, 2)
    status = f"{title}  markers {len(seen)}  " + (f"reproj {pose.err_px:.2f}px" if pose else "NO POSE - click disabled")
    cv2.rectangle(out, (0, 0), (out.shape[1], 30), (0, 0, 0), -1)
    cv2.putText(out, status, (8, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, TEXT if pose else BAD, 2)
    return out


def main() -> None:
    use_utf8_stdout()
    ap = argparse.ArgumentParser(description="사진 클릭 → W 좌표")
    ap.add_argument("path", type=Path, help="사진 한 장 또는 회차 폴더")
    ap.add_argument("--model", type=Path, help="3D_model/<회차> — 주면 메시와, 안 주면 Z=0 평면과 교차")
    a = ap.parse_args()

    files = sorted(a.path.glob("*.jpg")) if a.path.is_dir() else [a.path]
    if not files:
        raise SystemExit(f"사진이 없다: {a.path}")
    est = wframe.load_estimator()
    mesh = None
    if a.model:
        glb = wframe.map_file(a.model)
        if glb is None or not wframe.has_to_w(a.model):
            raise SystemExit(f"{a.model} 에 모델 · to_W.json 이 둘 다 있어야 한다")
        print(f"메시 불러오는 중: {glb.name} …")
        mesh = wframe.load_mesh_w(glb, wframe.load_to_w(a.model))
    print(f"교차 대상: {'메시 ' + a.model.name if mesh else '판 윗면 Z=0'}   (n 다음 · p 이전 · c 지우기 · q 끝)")

    k, picks, state = 0, [], {}

    def load(i: int) -> None:
        img = cv2.imread(str(files[i]))
        seen = est.detect(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        pose = est.estimate(seen) if len(seen) >= wframe.MIN_MARKERS else None
        state.update(img=img, seen=seen, pose=pose, title=f"{files[i].name} [{i + 1}/{len(files)}]")

    def on_mouse(event, x, y, *_):
        if event != cv2.EVENT_LBUTTONDOWN or state["pose"] is None:
            return
        p = pick((x, y), state["pose"], est, mesh)
        picks.append(p)
        where = "빗나감 (메시에 안 맞음)" if p.xyz is None else "W = ({:.4f}, {:.4f}, {:.4f}) m".format(*p.xyz)
        print(f"{files[k].name}  픽셀 ({x}, {y})  →  {where}")

    load(k)
    cv2.namedWindow(WIN)
    cv2.setMouseCallback(WIN, on_mouse)
    while True:
        cv2.imshow(WIN, render(state["img"], state["seen"], state["pose"], picks, state["title"]))
        key = cv2.waitKey(30) & 0xFF
        if key in (ord("q"), 27):
            break
        if key in (ord("n"), ord(" "), ord("p")):
            k = (k + (-1 if key == ord("p") else 1)) % len(files)
            picks.clear()
            load(k)
        elif key == ord("c"):
            picks.clear()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
