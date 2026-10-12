"""core/wframe — 실제 회차(perfect_ODM, 순간접착 고정 후 마커 10개 · 건물)의 3D 맵을 W 로 옮겨 마커 자리에 붙는지.

회차 폴더가 없으면 건너뛴다 (3D_model 은 저장소에 없다).
"""
import json
from pathlib import Path

import numpy as np
import pytest

from src.config import MODELS_DIR, SESSIONS_DIR
from src.core import wframe
from src.gl import loader

RUN = MODELS_DIR / "perfect_ODM"               # markers_W.json 과 같은 마커로 찍은 회차여야 한다
                                                # (000331_plus 는 ID 11 · 12 추가 전 배치표로 정합해 ID 0 이 +11.7mm,
                                                #  130739 는 2026-10-11 마커 고정 전 판이라 ID 0 이 +11mm)
pytestmark = pytest.mark.skipif(not wframe.has_to_w(RUN), reason=f"회차 없음: {RUN}")


def test_markers_sit_on_mesh():
    """마커 중심에서 수직으로 쏜 광선이 W 메시와 만나는 높이 = 배치표 높이 (정합 잔차 수준, 1cm 안).

    perfect_ODM 은 방 전체를 재구성해 판 위 면이 6.7k 뿐이라 메시 요철이 크다 — 정합 잔차 최대 6.8mm 에
    마커 5 가 7.7mm 로 나온다. 130739 에서 쓰던 7mm 대신 자동 정합 상한(`MAX_ALIGN_MEDIAN_MM`)과 같은 1cm.
    """
    mesh = wframe.load_mesh_w(RUN / "model.glb", wframe.load_to_w(RUN))
    for i, corners in wframe.load_estimator().layout.items():
        c = corners.mean(0)
        t = mesh.intersect(c + [0, 0, 1], np.array([[0, 0, -1.0]]))[0]
        assert abs(t - 1) < 0.010, f"마커 {i}"          # 1m 위에서 쐈다


def test_model_to_w_board_is_level():
    """화면용 모델도 같은 변환 — 판 안쪽 정점의 **최빈 높이**가 0 근처 (판이 수평, 윗면 Z=0).
    중앙값이 아니다: 건물 · 피규어가 판을 덮으면 중앙값이 뜬다 (000331_plus 에서 +12mm)."""
    m = wframe.model_to_w(loader.load(RUN / "model.glb"), wframe.load_to_w(RUN))
    P = np.vstack([p.positions for p in m.parts])
    inside = (P[:, :2] > 0.05).all(1) & (P[:, :2] < 1.15).all(1)
    h, edges = np.histogram(P[inside, 2], bins=np.arange(-0.05, 0.6, 0.002))
    assert abs(edges[h.argmax()] + 0.001) < 0.01
    n = np.vstack([p.normals for p in m.parts])
    assert np.allclose(np.linalg.norm(n, axis=1), 1, atol=1e-3)


def _subset_run(tmp_path: Path, every: int = 4) -> Path:
    """RUN 의 ODM 산출물에서 사진을 `every` 장마다 하나만 남긴 회차 폴더 — 정합 140초를 35초로."""
    g = json.loads((RUN / "shots.geojson").read_text())
    g["features"] = g["features"][::every]
    (tmp_path / "shots.geojson").write_text(json.dumps(g))
    (tmp_path / "cameras.json").write_text((RUN / "cameras.json").read_text())
    return tmp_path


def test_make_to_w_matches_cli_result(tmp_path):
    """앱의 자동 정합(`make_to_w`)이 CLI(align_model.py)로 만든 RUN 의 to_W.json 과 같은 W 를 낸다.

    사진을 1/4 만 써도 마커 자리(ODM → W)가 3mm 안에서 같아야 한다."""
    d = _subset_run(tmp_path)
    s = wframe.make_to_w(d, images=SESSIONS_DIR / RUN.name)
    assert s.median_mm < 5 and len(s.markers) >= 8
    assert wframe.has_to_w(d)
    M_cli, M_app = wframe.load_to_w(RUN), wframe.load_to_w(d)
    centers_w = np.array([c.mean(0) for c in wframe.load_estimator().layout.values()])
    centers_odm = wframe.apply(np.linalg.inv(M_cli), centers_w)
    assert np.abs(wframe.apply(M_app, centers_odm) - centers_w).max() < 0.003


def test_make_to_w_refuses_bad_alignment(tmp_path, monkeypatch):
    """잔차가 상한을 넘으면 파일을 만들지 않는다 — 틀린 행렬로 자동 자르기가 되면 판이 잘린다."""
    import align_model
    fake = align_model.ToW(1.0, np.eye(3), np.zeros(3), [(0, 0)] * 4, np.full(4, 25.0), {})
    monkeypatch.setattr(align_model, "compute", lambda *a, **k: fake)
    with pytest.raises(ValueError, match="25.0mm"):
        wframe.make_to_w(tmp_path, images=tmp_path)
    assert not wframe.has_to_w(tmp_path)


def test_can_align_needs_odm_pose_files(tmp_path):
    assert not wframe.can_align(tmp_path)
    (tmp_path / "shots.geojson").write_text("{}")
    assert not wframe.can_align(tmp_path)
    (tmp_path / "cameras.json").write_text("{}")
    assert wframe.can_align(tmp_path)


def test_auto_crop_in_w(tmp_path):
    """④ 자동 크롭 — 판정은 W 상자, 저장은 ODM 좌표. 다시 W 로 옮기면 상자 안.

    경계 삼각형 여유: 크롭은 **중심이 상자 안**인 삼각형을 통째로 남긴다. 중심에서 꼭짓점까지는
    가장 긴 변의 2/3 를 넘지 못하므로 그만큼 삐져나올 수 있다. 메시 해상도에 따라 달라
    고정값으로 두지 않는다 (000331_plus 는 최대변 73mm, 130739 는 122mm — 실제로 37mm 나갔다).
    """
    from src.core import cropper
    M = wframe.load_to_w(RUN)
    r = cropper.crop(cropper.load_scene(RUN / "model.glb"), tmp_path / "c.glb",
                     wframe.CROP_LO, wframe.CROP_HI, to_w=M, pad=0.0)
    assert 0 < r.faces_after < r.faces_before * 0.5          # 받침대 · 바닥이 떨어져 나간다
    m = wframe.model_to_w(loader.load(r.out), M)
    tri = np.vstack([p.positions[p.indices].reshape(-1, 3, 3) for p in m.parts])
    tol = np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2).max() * 2 / 3
    assert (m.lo >= wframe.CROP_LO - tol).all() and (m.hi <= wframe.CROP_HI + tol).all()


def test_victims_merge_same_class_within_radius():
    v = wframe.Victims(radius={"person": 0.03, "vehicle": 0.03})
    v.add("person", (0.50, 0.50, 0), "t1")
    v.add("person", (0.52, 0.50, 0), "t2")         # 2cm — 같은 사람, 위치는 평균
    v.add("person", (0.60, 0.50, 0), "t3")         # 10cm — 다른 사람
    v.add("vehicle", (0.50, 0.50, 0), "t4")        # 같은 자리라도 클래스가 다르면 따로
    assert [(x.cls, x.hits) for x in v.items] == [("person", 2), ("person", 1), ("vehicle", 1)]
    assert np.allclose(v.items[0].xyz, (0.51, 0.50, 0)) and v.items[0].last == "t2"


def test_victims_radius_is_per_class():
    v = wframe.Victims()
    v.add("vehicle", (0.50, 0.50, 0), "t1")
    v.add("vehicle", (0.60, 0.50, 0), "t2")        # 10cm — 차량(12cm)이면 같은 것
    v.add("casualty", (0.50, 0.80, 0), "t3")
    v.add("casualty", (0.60, 0.80, 0), "t4")       # 10cm — 피규어(8cm)면 다른 것
    assert [(x.cls, x.hits) for x in v.items] == [("vehicle", 2), ("casualty", 1), ("casualty", 1)]


def test_victims_keep_floors_apart():
    """같은 자리(수평)라도 높이 차가 MERGE_DZ 를 넘으면 다른 층의 다른 사람."""
    v = wframe.Victims()
    v.add("casualty", (0.30, 0.90, 0.20), "t1")
    v.add("casualty", (0.30, 0.90, 0.33), "t2")
    assert len(v.items) == 2


def test_victims_remerge_split_object_into_first():
    """시점 편향으로 갈라졌던 둘(10cm)이 관측이 쌓여 다가오면 합친다 — 먼저 생긴 쪽에."""
    v = wframe.Victims(radius={"casualty": 0.08})
    first = v.add("casualty", (0.50, 0.50, 0), "t1")
    v.add("casualty", (0.60, 0.50, 0), "t2")       # 10cm — 처음엔 따로
    assert len(v.items) == 2
    v.add("casualty", (0.53, 0.50, 0), "t3")       # 둘째 평균을 끌어당겨... 첫째에 붙는다
    v.add("casualty", (0.55, 0.50, 0), "t4")
    assert len(v.items) == 1 and v.items[0] is first and first.hits == 4 and first.last == "t4"


OBS = Path(__file__).parent / "data" / "obs_20261004_130739.npz"


def test_victims_count_matches_board_on_real_session():
    """회귀 — 세션 사진 468장의 실제 관측(YOLO 640 · 신뢰도 0.5 · 폰 K)을 그대로 넣으면
    판의 실제 개수가 나온다: 요구조자 23~25, 차량 5, 안전 9. 3cm 공통 반경이던 때는 78 · 25 · 49 였다.
    관측 파일은 맵과 무관하게 저장소에 있다 (YOLO 를 다시 돌리지 않는다)."""
    d = np.load(OBS)
    v = wframe.Victims()
    for c, p in zip(d["cls"], d["xyz"]):
        v.add(str(c), p.astype(float), "")
    count = lambda c: sum(1 for x in v.items if x.cls == c and x.hits >= 10)    # MIN_HITS (pages/detect.py)
    assert 23 <= count("casualty") <= 25
    assert count("vehicle") == 5
    assert count("person") == 9


# ---- 카메라 위치 · 촬영 범위 ----

def _top_down(est_seen: int = 4):
    """판 중심 (0.6, 0.6) 1m 위에서 똑바로 내려다보는 가짜 추정기 (fx=fy=1000, 주점 = 화면 중심 근처, 왜곡 없음)."""
    from types import SimpleNamespace
    K = np.array([[1000.0, 0, 640], [0, 1000.0, 480], [0, 0, 1]])
    R = np.array([[1.0, 0, 0], [0, -1, 0], [0, 0, -1]])     # W→카메라: 카메라 z(앞) = W -z, 영상 아래 = W -y
    pose = wframe.Pose(R, np.array([0.6, 0.6, 1.0]), list(range(est_seen)), 0.5)
    seen = {i: None for i in range(est_seen)}
    return SimpleNamespace(K=K, dist=np.zeros(5), detect=lambda g: seen, estimate=lambda s: pose)


def _plate(lo: float, hi: float) -> wframe.Mesh:
    """Z=0 의 정사각형 판 (삼각형 2개)."""
    v = np.array([[lo, lo, 0], [hi, lo, 0], [hi, hi, 0], [lo, hi, 0]], float)
    return wframe.Mesh(v, np.array([[0, 1, 2], [0, 2, 3]]))


def test_locate_reports_camera_and_footprint_even_off_mesh():
    """판(0.5~0.7)이 작아 모서리 광선은 빗나간다 → 바닥 Z=0 으로 떨어뜨린 사각형. 박스 중심은 판에 맞는다."""
    gray = np.zeros((960, 1280), np.uint8)
    loc = wframe.locate(gray, np.array([[640.0, 480.0]]), _top_down(), _plate(0.5, 0.7))
    assert loc.markers == 4
    assert np.allclose(loc.camera.C, (0.6, 0.6, 1.0))
    assert np.allclose(loc.points, [[0.6, 0.6, 0.0]])
    # 좌상 → 우상 → 우하 → 좌하. 영상 위쪽 = W +y
    want = [[0.6 - 0.640, 0.6 + 0.480, 0], [0.6 + 0.639, 0.6 + 0.480, 0],
            [0.6 + 0.639, 0.6 - 0.479, 0], [0.6 - 0.640, 0.6 - 0.479, 0]]
    assert np.allclose(loc.camera.footprint, want, atol=1e-6)


def test_locate_without_pose_has_no_camera_or_points():
    loc = wframe.locate(np.zeros((960, 1280), np.uint8), np.zeros((1, 2)), _top_down(est_seen=1), _plate(0, 1))
    assert (loc.markers, loc.camera, loc.points) == (1, None, None)


def test_grazing_corner_rays_stop_at_far_limit():
    """거의 수평인 광선은 바닥과 아주 멀리서 만난다 — FOOTPRINT_FAR 에서 자른다."""
    C = np.array([0.0, 0.0, 1.0])
    d = np.array([[1.0, 0, -1e-4], [0, 0, 1.0]])
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    out = wframe._land(C, d, np.array([np.inf, np.inf]))
    assert np.allclose(np.linalg.norm(out - C, axis=1), wframe.FOOTPRINT_FAR)


# ---- 구역 ----

@pytest.mark.parametrize("xyz, want", [
    ((0.0, 0.0, 0), "A-1"), ((0.29, 0.31, 0), "A-2"), ((0.3, 0.0, 0), "B-1"),     # 경계는 위쪽 칸
    ((1.2, 1.2, 0), "D-4"),                                                       # 판 끝은 마지막 칸
    ((-0.01, 0.5, 0), None), ((0.5, 1.21, 0), None),                              # 판 밖
])
def test_sector_names(xyz, want):
    assert wframe.sector(xyz) == want


def test_height_text_splits_ground():
    assert wframe.height_text(0.02) == "지면" and wframe.height_text(0.46) == "높이 0.46m"


def test_sector_grid_has_lines_and_labels():
    seg, labels = wframe.sector_grid()
    assert seg.shape == (10, 2, 3)                  # 세로 5 + 가로 5
    assert [t for _, t in labels] == ["A", "B", "C", "D", "1", "2", "3", "4"]


# ---- 드론 자세 ----

def test_drone_body_follows_horizontal_gaze_and_stays_level():
    """카메라가 +X 쪽을 30° 내려다보면 → 기수 +X, 기체는 수평 (Z 위)."""
    a = np.radians(30)
    fwd = np.array([np.cos(a), 0, -np.sin(a)])      # 카메라 z(앞)
    right = np.array([0, -1.0, 0])                   # 카메라 x(오른쪽)
    down = np.cross(fwd, right)                      # 카메라 y(아래) = z × x
    B = wframe.drone_body(np.vstack([right, down, fwd]))
    assert np.allclose(B[:, 1], [1, 0, 0]) and np.allclose(B[:, 2], [0, 0, 1])
    assert np.allclose(B.T @ B, np.eye(3)) and np.isclose(np.linalg.det(B), 1)


def test_drone_body_looking_straight_down_uses_image_up_as_forward():
    """수직 하향(_top_down) — 영상 위쪽 = W +y 가 앞."""
    R = np.array([[1.0, 0, 0], [0, -1, 0], [0, 0, -1]])
    assert np.allclose(wframe.drone_body(R)[:, 1], [0, 1, 0])
