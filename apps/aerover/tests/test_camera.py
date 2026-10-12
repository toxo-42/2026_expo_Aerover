"""gl/camera — 투영이 화면 좌표와 맞는지 (호버 판정이 이것에 기댄다)."""
import numpy as np

from src.gl.camera import OrbitCamera


def test_project_top_view_puts_target_at_center_and_axes_right_way():
    cam = OrbitCamera()
    cam.fit((0, 0, 0), (1.2, 1.2, 0.5), aspect=800 / 600)
    cam.look_down()
    cam.yaw = -np.pi / 2            # 눈이 -Y 쪽 위 → 화면 오른쪽 = +X, 화면 위 = +Y
    t = cam.target
    px = cam.project(np.array([[t.x(), t.y(), t.z()], [t.x() + 0.3, t.y(), t.z()],
                               [t.x(), t.y() + 0.3, t.z()]]), 800, 600)
    assert np.allclose(px[0], (400, 300), atol=0.5)
    assert px[1, 0] > 400 + 10 and abs(px[1, 1] - 300) < 2      # +X → 오른쪽
    assert px[2, 1] < 300 - 10                                   # +Y → 위


def test_project_behind_camera_is_nan():
    cam = OrbitCamera()
    cam.fit((0, 0, 0), (1, 1, 1))
    e, t = cam.eye(), cam.target
    behind = 2 * np.array([e.x(), e.y(), e.z()]) - np.array([t.x(), t.y(), t.z()])
    assert np.isnan(cam.project(behind[None], 800, 600)).all()


def test_world_per_px_grows_with_depth_and_matches_projection():
    """깊이가 두 배면 1픽셀의 실제 길이도 두 배. 그 길이만큼 옆으로 옮긴 점은 화면에서 1픽셀 옮겨진다."""
    cam = OrbitCamera()
    cam.fit((0, 0, 0), (1, 1, 0), aspect=800 / 600)
    e, t = cam.eye(), cam.target
    eye, tgt = np.array([e.x(), e.y(), e.z()]), np.array([t.x(), t.y(), t.z()])
    near, far = eye + (tgt - eye) * 0.5, eye + (tgt - eye) * 1.0
    w = cam.world_per_px(np.array([near, far]), 600)
    assert np.isclose(w[1] / w[0], 2.0)
    right = np.cross(tgt - eye, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    px = cam.project(np.array([far, far + right * w[1] * 10]), 800, 600)
    assert np.isclose(np.linalg.norm(px[1] - px[0]), 10, atol=0.05)


def test_marker_mesh_scales_each_pin_to_its_height():
    """핀마다 높이를 따로 받는다 — 꼭짓점은 점에서 시작해 그 높이에서 끝난다."""
    from src.gl.viewport import marker_mesh
    pts = np.array([[0, 0, 0], [1, 0, 0]], float)
    m = marker_mesh(pts, [(255, 0, 0), (0, 0, 255)], np.array([0.1, 0.3]))
    p = m.positions.reshape(2, -1, 3)
    assert np.allclose(p[:, :, 2].max(1), [0.1, 0.3]) and np.allclose(p[:, :, 2].min(1), 0)
    assert len(m.indices) == 2 * 8 * 3 and m.texture.shape == (1, 2, 3)
