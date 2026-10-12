"""tools/fake_pi — ODM 카메라 → K 변환, 그리고 앱의 RtpLinkWorker 가 실제로 붙어 프레임을 받는지."""
import json
import socket
import threading
import time

import cv2
import numpy as np
import pytest

pytest.importorskip("pymavlink")
QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6.QtCore import QCoreApplication, Qt     # noqa: E402

from src.config import MODELS_DIR, SESSIONS_DIR     # noqa: E402
from src.core.link import RtpLinkWorker             # noqa: E402
from tools import fake_pi                           # noqa: E402

RUN = "perfect_ODM"      # 폰 회차. markers_W.json 과 같은 판 상태여야 한다


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def _wait(cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_odm_camera_scales_normalized_values_by_long_side():
    cam = {"projection_type": "brown", "width": 4000, "height": 3000,
           "focal_x": 0.5, "focal_y": 0.5, "c_x": 0.01, "c_y": -0.02,
           "k1": 0.1, "k2": 0.2, "p1": 0.3, "p2": 0.4, "k3": 0.5}
    K, dist = fake_pi.odm_camera(cam, (1280, 960))
    assert np.allclose(K, [[640, 0, 640 + 12.8], [0, 640, 480 - 25.6], [0, 0, 1]])
    assert dist.tolist() == [0.1, 0.2, 0.3, 0.4, 0.5]
    with pytest.raises(ValueError):
        fake_pi.odm_camera({**cam, "width": 1920, "height": 1080})     # 16:9 사진은 4:3 스트림이 안 된다


@pytest.mark.skipif(not (MODELS_DIR / RUN / "cameras.json").is_file(), reason=f"회차 없음: {RUN}")
def test_replay_camera_solves_pose_of_session_photo(tmp_path):
    """폰 K 로 바꾸면 재생 프레임의 자세가 2~4px 오차로 풀린다 (파이캠 K 면 14px)."""
    from src.core import wframe
    src = tmp_path / "cameras.json"
    src.write_text((MODELS_DIR / RUN / "cameras.json").read_text())
    est = wframe.load_estimator(fake_pi.write_replay_camera(src))
    photo = fake_pi.list_images(SESSIONS_DIR / RUN)[0]
    gray = cv2.imdecode(np.frombuffer(fake_pi.encode_frame(photo), np.uint8), cv2.IMREAD_GRAYSCALE)
    assert gray.shape == (960, 1280)
    pose = est.estimate(est.detect(gray))
    assert pose is not None and pose.err_px < 5


def test_link_worker_receives_replayed_frames(app, tmp_path):
    for i in range(2):                              # 4:3 사진 두 장 — 색으로 순서를 구분한다
        cv2.imwrite(str(tmp_path / f"{i}.jpg"), np.full((60, 80, 3), 60 + 120 * i, np.uint8))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        rtp_port = s.getsockname()[1]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.005)
    node = fake_pi.FakePiNode(sock, fake_pi.list_images(tmp_path), fps=50, rtp_port=rtp_port)
    stop = threading.Event()

    def pump() -> None:
        while not stop.is_set():
            node.step(time.monotonic())

    th = threading.Thread(target=pump, daemon=True)
    th.start()

    got, events = [], []
    w = RtpLinkWorker("127.0.0.1", sock.getsockname()[1], rtp_port=rtp_port)
    w.frame_ready.connect(lambda img, raw: got.append((img.width(), img.height())),
                          Qt.ConnectionType.DirectConnection)
    w.connected.connect(lambda: events.append("connected"), Qt.ConnectionType.DirectConnection)
    w.failed.connect(events.append, Qt.ConnectionType.DirectConnection)
    w.start()
    try:
        assert _wait(lambda: len(got) >= 3), events
        assert events == ["connected"]
        assert set(got) == {(1280, 960)}
        assert node.index >= 1 and node.streaming
    finally:
        w.stop()
        assert w.wait(5000)
        assert _wait(lambda: not node.streaming, 3.0)     # 끊을 때 STOP 을 받는다
        stop.set()
        th.join(2.0)
        sock.close()


def test_replay_camera_file_is_what_pose_loader_reads(tmp_path):
    src = tmp_path / "cameras.json"
    src.write_text(json.dumps({"x": {"projection_type": "brown", "width": 4, "height": 3,
                                     "focal_x": 1, "focal_y": 1, "c_x": 0, "c_y": 0,
                                     "k1": 0, "k2": 0, "p1": 0, "p2": 0, "k3": 0}}))
    out = fake_pi.write_replay_camera(src)
    d = json.loads(out.read_text())
    assert out.name == "replay_camera.json" and d["image_size"] == [1280, 960]
    assert np.array(d["K"]).shape == (3, 3) and len(d["dist"]) == 5
