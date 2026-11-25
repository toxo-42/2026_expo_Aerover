import socket
import time

import cv2
import numpy as np
import pytest

pytest.importorskip("pymavlink")
QtCore = pytest.importorskip("PySide6.QtCore")
from PySide6.QtCore import QCoreApplication, Qt     # noqa: E402

from src.core.link import RtpLinkWorker             # noqa: E402
from src.core.replay import ReplayServer, encode_frame  # noqa: E402


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


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_stream_size_jpeg_is_sent_as_is(tmp_path):
    """파이 수집본(1280×960)은 1/2 로 읽고 키우지 않고 파일 그대로 보낸다."""
    path = tmp_path / "000.jpg"
    cv2.imwrite(str(path), np.random.default_rng(0).integers(0, 255, (960, 1280, 3), np.uint8))
    assert encode_frame(path) == path.read_bytes()


def test_small_photo_is_read_full_then_scaled(tmp_path):
    path = tmp_path / "000.png"
    cv2.imwrite(str(path), np.full((480, 640, 3), 128, np.uint8))
    img = cv2.imdecode(np.frombuffer(encode_frame(path), np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (960, 1280, 3)


def test_server_refuses_empty_folder(tmp_path):
    with pytest.raises(ValueError):
        ReplayServer(tmp_path, 5).start()
    with pytest.raises(ValueError):
        ReplayServer(tmp_path / "없음", 5).start()


def test_link_worker_plays_replay_server(app, tmp_path):
    for i in range(2):
        cv2.imwrite(str(tmp_path / f"{i}.jpg"), np.full((960, 1280, 3), 60 + 120 * i, np.uint8))
    rtp_port = _free_udp_port()
    server = ReplayServer(tmp_path, fps=50, rtp_port=rtp_port)
    port = server.start()

    got, events = [], []
    w = RtpLinkWorker("127.0.0.1", port, rtp_port=rtp_port)
    w.frame_ready.connect(lambda img, raw: got.append((img.width(), img.height(), img.pixelColor(640, 480).red())),
                          Qt.ConnectionType.DirectConnection)
    w.connected.connect(lambda: events.append("connected"), Qt.ConnectionType.DirectConnection)
    w.failed.connect(events.append, Qt.ConnectionType.DirectConnection)
    w.start()
    try:
        assert _wait(lambda: len(got) >= 3), events
        assert events == ["connected"]
        assert {(w_, h_) for w_, h_, _ in got} == {(1280, 960)}
        assert {round(r, -1) for _, _, r in got} == {60, 180}      # 두 장을 번갈아 보낸다
    finally:
        w.stop()
        assert w.wait(5000)
        server.stop()
    assert server._thread is None
