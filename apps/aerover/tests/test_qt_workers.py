"""Qt 스레드 워커 — 화면 없이 QCoreApplication 만으로 돌린다."""
import io
from pathlib import Path
import socket
import tempfile
import threading
import time

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
from PIL import Image                                      # noqa: E402
from PySide6.QtCore import QCoreApplication, Qt            # noqa: E402
from PySide6.QtGui import QImage                           # noqa: E402

from src.core import telemetry as tm                       # noqa: E402
from PySide6.QtGui import QColor                           # noqa: E402
from src.core.detect import (COCO_KEEP, DetectWorker, YoloDetector,  # noqa: E402
                             is_coco, qimage_to_bgr)
from src.core.framing import length_prefixed               # noqa: E402
from src.core.link import LinkHub, LinkWorker, RtpLinkWorker, decode_jpeg  # noqa: E402
from src.core.rtpjpeg import RtpJpegPacketizer             # noqa: E402
from tools.labelio import Box as LabelBox, dump, load as load_labels, pick, zoom_at  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def _jpeg(w: int = 8, h: int = 6) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


def _serve(frames: list[bytes], keep_open: float = 0.0) -> tuple[socket.socket, int]:
    """프레임을 보내고 닫는 가짜 파이 (TCP). (서버 소켓, 포트) 를 돌려준다."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        for f in frames:
            conn.sendall(length_prefixed(f))
        time.sleep(keep_open)
        conn.close()

    threading.Thread(target=run, daemon=True).start()
    return srv, srv.getsockname()[1]


def _wait(cond, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---- TCP ----

def test_decode_jpeg_rejects_garbage(app):
    assert decode_jpeg(b"nope") is None
    assert decode_jpeg(_jpeg()).width() == 8


def test_link_worker_receives_frames_and_skips_broken_ones(app):
    srv, port = _serve([_jpeg(), b"broken", _jpeg(10, 4)])
    got, events = [], []
    w = LinkWorker("127.0.0.1", port)
    w.frame_ready.connect(lambda img, raw: got.append((img.width(), raw)), Qt.ConnectionType.DirectConnection)
    w.connected.connect(lambda: events.append("connected"), Qt.ConnectionType.DirectConnection)
    w.failed.connect(lambda m: events.append(m), Qt.ConnectionType.DirectConnection)
    w.start()
    assert w.wait(5000)
    srv.close()
    assert events == ["connected"]
    assert [wd for wd, _ in got] == [8, 10]
    assert got[0][1] == _jpeg()


def test_link_worker_reports_connect_failure(app):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()                               # 아무도 안 듣는 포트
    events = []
    w = LinkWorker("127.0.0.1", port)
    w.failed.connect(events.append, Qt.ConnectionType.DirectConnection)
    w.start()
    assert w.wait(5000)
    assert events and events[0].startswith("링크 연결 실패")


def test_stop_before_run_is_not_lost(app):
    w = LinkWorker("127.0.0.1", 1)
    w.stop()
    events = []
    w.failed.connect(events.append, Qt.ConnectionType.DirectConnection)
    w.start()
    assert w.wait(2000) and events == []


def test_hub_owns_one_worker_and_closes(app):
    srv, port = _serve([_jpeg()], keep_open=5.0)
    hub = LinkHub(worker_factory=LinkWorker)
    closed = []
    hub.closed.connect(lambda: closed.append(1))
    frames = []
    hub.frame_ready.connect(lambda img, raw: frames.append(raw))

    hub.connect_to("127.0.0.1", port)
    first = hub._worker
    hub.connect_to("127.0.0.1", port)           # 이미 붙어 있으면 무시
    assert hub._worker is first and hub.active

    assert _wait(lambda: (app.processEvents(), bool(frames))[1])
    hub.disconnect_from()
    assert _wait(lambda: (app.processEvents(), bool(closed))[1])
    assert not hub.active
    srv.close()


# ---- UDP: MAVLink + RTP ----

class FakePi:
    """MAVLink 로 응답하고 RTP 프레임을 쏘는 가짜 파이 노드."""

    def __init__(self, frames: list[bytes], rtp_port: int, reject: bool = False) -> None:
        from pymavlink.dialects.v20 import common as mav
        self.mav = mav
        self.frames, self.rtp_port, self.reject = frames, rtp_port, reject
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.starts = self.stops = 0
        self._link = mav.MAVLink(None, srcSystem=1, srcComponent=mav.MAV_COMP_ID_CAMERA)
        self._link.robust_parsing = True
        self._fc = mav.MAVLink(None, srcSystem=1, srcComponent=1)
        self._run = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self._run = False
        self.thread.join(2.0)
        self.sock.close()

    def _loop(self) -> None:
        mav = self.mav
        while self._run:
            try:
                data, addr = self.sock.recvfrom(4096)
            except OSError:                 # 타임아웃, 또는 윈도우가 ICMP 도달 불가를 예외로 올린 것
                continue
            for m in self._link.parse_buffer(data) or []:
                if m.get_type() == "HEARTBEAT":
                    hb = self._link.heartbeat_encode(mav.MAV_TYPE_CAMERA, mav.MAV_AUTOPILOT_INVALID, 0, 0, 4)
                    self.sock.sendto(hb.pack(self._link), addr)
                elif m.get_type() == "COMMAND_LONG" and m.command == mav.MAV_CMD_VIDEO_START_STREAMING:
                    self.starts += 1
                    if self.reject:
                        self.sock.sendto(self._link.statustext_encode(mav.MAV_SEVERITY_ERROR, b"camera busy").pack(self._link), addr)
                        self.sock.sendto(self._link.command_ack_encode(m.command, mav.MAV_RESULT_FAILED).pack(self._link), addr)
                        continue
                    self.sock.sendto(self._link.command_ack_encode(m.command, mav.MAV_RESULT_ACCEPTED).pack(self._link), addr)
                    gps = self._fc.gps_raw_int_encode(0, 3, 375665000, 1269780000, 52000, 0, 0, 0, 0, 9)
                    self.sock.sendto(gps.pack(self._fc), addr)
                    if self.starts == 1:
                        pk = RtpJpegPacketizer(mtu=1200)
                        for f in self.frames:
                            for p in pk.packets(f):
                                self.sock.sendto(p, (addr[0], self.rtp_port))
                elif m.get_type() == "COMMAND_LONG" and m.command == mav.MAV_CMD_VIDEO_STOP_STREAMING:
                    self.stops += 1
                    self.sock.sendto(self._link.command_ack_encode(m.command, mav.MAV_RESULT_ACCEPTED).pack(self._link), addr)


def test_rtp_link_worker_handshakes_receives_frames_and_telemetry(app):
    pytest.importorskip("pymavlink")
    rtp_port = _free_udp_port()
    pi = FakePi([_jpeg(16, 8), _jpeg(24, 16)], rtp_port)
    got, events = [], []
    w = RtpLinkWorker("127.0.0.1", pi.port, rtp_port=rtp_port)
    w.frame_ready.connect(lambda img, raw: got.append((img.width(), raw)), Qt.ConnectionType.DirectConnection)
    w.connected.connect(lambda: events.append("connected"), Qt.ConnectionType.DirectConnection)
    w.failed.connect(events.append, Qt.ConnectionType.DirectConnection)
    w.start()
    try:
        assert _wait(lambda: len(got) >= 2, 5.0), events
        assert events == ["connected"]
        assert [wd for wd, _ in got] == [16, 24]
        assert all(raw[:2] == b"\xff\xd8" and raw[-2:] == b"\xff\xd9" for _, raw in got)
        assert tm.get_telemetry()["gps"]["sats"] == 9           # 파이가 중계한 FC 텔레메트리
        assert w.depacketizer.stats.frames == 2
    finally:
        w.stop()
        assert w.wait(5000)
        assert _wait(lambda: pi.stops >= 1, 3.0)                # 끊을 때 STOP 을 보낸다
        pi.close()


def test_rtp_link_worker_fails_when_pi_is_silent(app):
    pytest.importorskip("pymavlink")
    events = []
    w = RtpLinkWorker("127.0.0.1", _free_udp_port(), rtp_port=_free_udp_port())
    w.CONNECT_TIMEOUT = 0.5
    w.failed.connect(events.append, Qt.ConnectionType.DirectConnection)
    w.start()
    assert w.wait(5000)
    assert events and events[0].startswith("파이 응답 없음")


def test_rtp_link_worker_reports_pi_rejection(app):
    pytest.importorskip("pymavlink")
    rtp_port = _free_udp_port()
    pi = FakePi([], rtp_port, reject=True)
    events = []
    w = RtpLinkWorker("127.0.0.1", pi.port, rtp_port=rtp_port)
    w.failed.connect(events.append, Qt.ConnectionType.DirectConnection)
    w.start()
    try:
        assert w.wait(5000)
        assert events and events[-1] == "파이가 스트리밍을 거부했다 — camera busy"
    finally:
        pi.close()


# ---- 탐지 ----

def test_detect_worker_keeps_only_latest_frame_and_filters_by_conf(app):
    seen, results, errors = [], [], []

    def detector(frame: QImage):
        seen.append(frame.width())
        time.sleep(0.05)
        return [("person", 0.9, 0, 0, 1, 1), ("person", 0.2, 0, 0, 1, 1)]

    w = DetectWorker(conf=0.5, detector=detector)
    w.result.connect(results.append, Qt.ConnectionType.DirectConnection)
    w.failed.connect(errors.append, Qt.ConnectionType.DirectConnection)
    w.start()
    for width in (1, 2, 3):
        w.submit(QImage(width, 1, QImage.Format.Format_RGB888))
    assert _wait(lambda: len(results) >= 1)
    w.stop()
    assert w.wait(2000)
    assert results[0] == [("person", 0.9, 0, 0, 1, 1)]
    assert len(seen) <= 2 and seen[-1] == 3 and not errors     # 중간 프레임은 버려진다


def test_detect_worker_reports_detector_error(app):
    errors = []

    def broken(frame):
        raise RuntimeError("no model")

    w = DetectWorker(conf=0.5, detector=broken)
    w.failed.connect(errors.append, Qt.ConnectionType.DirectConnection)
    w.start()
    w.submit(QImage(1, 1, QImage.Format.Format_RGB888))
    assert w.wait(2000)
    assert errors == ["탐지 중단 — no model"]


def test_qimage_to_bgr_matches_pixels_despite_row_padding():
    # 폭 3 → 한 줄이 9바이트인데 QImage 는 4바이트로 정렬해 12바이트를 쓴다.
    # 패딩을 안 잘라내면 두 번째 줄부터 색이 밀린다.
    img = QImage(3, 2, QImage.Format.Format_RGB888)
    img.fill(QColor(10, 20, 30))
    arr = qimage_to_bgr(img)
    assert arr.shape == (2, 3, 3)
    assert arr[1, 2].tolist() == [30, 20, 10]       # BGR 로 뒤집혀 있다 (ultralytics 규약)
    assert arr.flags["C_CONTIGUOUS"]                # torch 가 연속 메모리를 요구한다


def test_yolo_detector_merges_coco_classes_but_passes_custom_ones_through():
    coco = YoloDetector(coco=True)
    assert coco.label("person") == "person"
    assert coco.label("bus") == "vehicle"
    assert coco.label("chair") is None              # 표에 없는 클래스는 버린다

    custom = YoloDetector(coco=False)               # 팀 학습본(best.pt)
    assert custom.label("chair") == "chair"

    assert is_coco({0: "person", 2: "car"}) and not is_coco({0: "survivor"})
    assert set(COCO_KEEP.values()) == {"person", "vehicle"}


def test_detect_worker_reports_model_open_failure(app):
    errors, ready = [], []

    class Failing:
        def load(self):
            raise FileNotFoundError("weights")

        def __call__(self, frame):                  # 불릴 일이 없다
            raise AssertionError

    w = DetectWorker(conf=0.5, detector=Failing())
    w.ready.connect(lambda: ready.append(1), Qt.ConnectionType.DirectConnection)
    w.failed.connect(errors.append, Qt.ConnectionType.DirectConnection)
    w.start()
    assert w.wait(2000)
    assert errors == ["모델을 열지 못했다 — weights"] and not ready


# ---- 라벨 도구 (순수 로직만 — 위젯은 QApplication 이 필요해 여기서 만들지 않는다) ----

def test_labelio_round_trip_and_clamping():
    text = dump([LabelBox(0, 10, 20, 110, 220), LabelBox(1, -50, -50, 1400, 1100)], 1280, 960)
    assert len(text.strip().splitlines()) == 2

    tmp = Path(tempfile.mkdtemp()) / "a.txt"
    tmp.write_text(text, encoding="utf-8")
    back = load_labels(tmp, 1280, 960)
    assert [round(v) for v in (back[0].x1, back[0].y1, back[0].x2, back[0].y2)] == [10, 20, 110, 220]
    # 이미지 밖으로 나간 상자는 가장자리에 맞춰 저장된다
    assert [round(v) for v in (back[1].x1, back[1].y1, back[1].x2, back[1].y2)] == [0, 0, 1280, 960]


def test_labelio_ignores_broken_lines():
    tmp = Path(tempfile.mkdtemp()) / "b.txt"
    tmp.write_text("0 0.5 0.5 0.1 0.1\n\n깨진줄\n1 0.2 0.2\n", encoding="utf-8")
    assert len(load_labels(tmp, 100, 100)) == 1


def test_labelio_drops_boxes_fully_outside():
    assert dump([LabelBox(0, -40, -40, -10, -10)], 100, 100) == ""


def test_labelio_normalizes_reverse_drag():
    box = LabelBox(0, 90, 80, 10, 20).normalized()
    assert (box.x1, box.y1, box.x2, box.y2) == (10, 20, 90, 80)


def test_pick_takes_smallest_box_covering_the_point():
    boxes = [LabelBox(0, 0, 0, 100, 100), LabelBox(0, 40, 40, 60, 60)]
    assert pick(boxes, 50, 50) == 1          # 큰 오탐 위에 겹친 작은 상자를 집는다
    assert pick(boxes, 5, 5) == 0
    assert pick(boxes, 200, 200) is None


def test_zoom_at_keeps_point_under_cursor():
    offset, zoom, cursor = (37.0, -12.0), 0.8, (500.0, 300.0)
    before = ((cursor[0] - offset[0]) / zoom, (cursor[1] - offset[1]) / zoom)
    new_offset = zoom_at(offset, zoom, zoom * 4, cursor)
    after = ((cursor[0] - new_offset[0]) / (zoom * 4), (cursor[1] - new_offset[1]) / (zoom * 4))
    assert abs(after[0] - before[0]) < 1e-6 and abs(after[1] - before[1]) < 1e-6
