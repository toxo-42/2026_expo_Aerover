"""UDP 링크 — rtp_output · rtp_session · camera_node · fc_bridge · 프로그램 조립."""
import io
import socket

import numpy as np
import pytest
from PIL import Image
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder

pytest.importorskip("pymavlink")
from pymavlink.dialects.v20 import common as mav      # noqa: E402

from src.control.camera_node import CMD_VIDEO_START, CMD_VIDEO_STOP, CameraNode   # noqa: E402
from src.control.fc_bridge import FcBridge, open_fc_bridge   # noqa: E402
from src.stream.rtp_output import RtpJpegOutput        # noqa: E402
from src.stream.rtp_session import RtpStreamSession    # noqa: E402
from src.stream.rtpjpeg import RtpJpegDepacketizer, RtpJpegPacketizer   # noqa: E402


def _jpeg(w: int = 160, h: int = 120) -> bytes:
    rng = np.random.default_rng(0)
    buf = io.BytesIO()
    Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8)).save(buf, "JPEG", quality=80)
    return buf.getvalue()


class Clock:
    def __init__(self) -> None:
        self.t = 50.0

    def __call__(self) -> float:
        return self.t


def _gcs_link() -> mav.MAVLink:
    link = mav.MAVLink(None, srcSystem=255, srcComponent=190)
    link.robust_parsing = True
    return link


def _gcs_heartbeat(link) -> bytes:
    return link.heartbeat_encode(mav.MAV_TYPE_GCS, mav.MAV_AUTOPILOT_INVALID, 0, 0, 4).pack(link)


def _command(link, cmd: int) -> bytes:
    return link.command_long_encode(1, mav.MAV_COMP_ID_CAMERA, cmd, 0, 1, 0, 0, 0, 0, 0, 0).pack(link)


# ---- rtp_output ----

def test_rtp_output_sends_packets_a_depacketizer_can_rebuild():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2.0)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    out = RtpJpegOutput(tx, rx.getsockname(), RtpJpegPacketizer(mtu=500))

    jpeg = _jpeg()
    assert out.write(bytearray(jpeg)) == len(jpeg)
    depack = RtpJpegDepacketizer()
    rebuilt = None
    while rebuilt is None:
        rebuilt = depack.push(rx.recv(2000))
    assert out.frames == 1 and out.packets == depack.stats.packets > 1
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(rebuilt))),
                          np.asarray(Image.open(io.BytesIO(jpeg))))
    rx.close(); tx.close()


def test_rtp_output_reports_unsupported_jpeg_and_socket_errors():
    errors = []
    out = RtpJpegOutput(socket.socket(socket.AF_INET, socket.SOCK_DGRAM), ("127.0.0.1", 1),
                        on_error=errors.append)
    assert out.write(b"not a jpeg") == 0
    assert out.errors == 1 and errors


# ---- rtp_session ----

def test_session_opens_camera_per_destination_and_releases():
    Picamera2.instances.clear()
    outputs = []

    def make_output(dest):
        outputs.append(dest)
        return io.BytesIO()

    s = RtpStreamSession(Picamera2, JpegEncoder, make_output)
    assert not s.streaming
    s.start(("10.0.0.2", 5004))
    first = Picamera2.instances[-1]
    assert s.streaming and first.recording and outputs == [("10.0.0.2", 5004)]

    s.start(("10.0.0.2", 5004))                      # 같은 곳 — 그대로
    assert Picamera2.instances[-1] is first and len(outputs) == 1

    s.start(("10.0.0.3", 5004))                      # 다른 곳 — 갈아탄다
    assert first.closed and Picamera2.instances[-1] is not first and len(outputs) == 2

    s.stop()
    assert not s.streaming and Picamera2.instances[-1].closed
    s.stop()                                         # 두 번 불러도 조용하다


def test_session_closes_camera_if_recording_fails():
    class Broken(Picamera2):
        def start_recording(self, encoder, output):
            raise RuntimeError("encoder busy")

    s = RtpStreamSession(Broken, JpegEncoder, lambda d: io.BytesIO())
    with pytest.raises(RuntimeError):
        s.start(("h", 1))
    assert Picamera2.instances[-1].closed and not s.streaming


# ---- camera_node ----

def _node(clock: Clock, on_start=None, on_stop=None):
    sent: list[tuple[bytes, tuple]] = []
    starts, stops = [], []
    node = CameraNode(send=lambda d, a: sent.append((d, a)),
                      on_start=on_start or starts.append, on_stop=on_stop or (lambda: stops.append(1)),
                      rtp_port=5004, gcs_timeout=3.0, clock=clock)
    return node, sent, starts, stops


def _parse_sent(sent, link) -> list:
    return [m for data, _ in sent for m in (link.parse_buffer(data) or [])]


def test_node_handshake_start_stop_and_ack():
    clock, link = Clock(), _gcs_link()
    node, sent, starts, stops = _node(clock)
    gcs = ("192.168.137.1", 40000)

    node.tick()                                      # 지상국을 모르면 조용하다
    assert sent == []

    node.receive(_gcs_heartbeat(link), gcs)
    assert node.gcs == gcs
    node.tick()
    assert sent[-1][1] == gcs
    hb = _parse_sent(sent, link)[-1]
    assert hb.get_type() == "HEARTBEAT" and hb.type == mav.MAV_TYPE_CAMERA and hb.get_srcComponent() == 100

    node.receive(_command(link, CMD_VIDEO_START), gcs)
    assert starts == [("192.168.137.1", 5004)] and node.streaming
    ack = _parse_sent(sent, link)[-1]
    assert ack.get_type() == "COMMAND_ACK" and ack.command == CMD_VIDEO_START
    assert ack.result == mav.MAV_RESULT_ACCEPTED and ack.target_system == 255

    node.receive(_command(link, CMD_VIDEO_STOP), gcs)
    assert stops == [1] and not node.streaming
    assert _parse_sent(sent, link)[-1].command == CMD_VIDEO_STOP


def test_node_stops_stream_when_gcs_heartbeat_times_out():
    clock, link = Clock(), _gcs_link()
    node, sent, starts, stops = _node(clock)
    gcs = ("10.0.0.9", 1)
    node.receive(_gcs_heartbeat(link), gcs)
    node.receive(_command(link, CMD_VIDEO_START), gcs)
    clock.t += 2.0
    node.receive(_gcs_heartbeat(link), gcs)          # 살아 있다
    clock.t += 2.5
    node.tick()
    assert node.streaming and stops == []
    clock.t += 1.0                                   # 마지막 HEARTBEAT 로부터 3.5초
    node.tick()
    assert stops == [1] and not node.streaming and node.gcs is None


def test_node_reports_camera_failure_with_ack_and_statustext():
    clock, link = Clock(), _gcs_link()

    def broken(dest):
        raise RuntimeError("camera busy")

    node, sent, _, _ = _node(clock, on_start=broken)
    gcs = ("10.0.0.9", 1)
    node.receive(_gcs_heartbeat(link), gcs)
    node.receive(_command(link, CMD_VIDEO_START), gcs)
    msgs = _parse_sent(sent, link)
    kinds = [m.get_type() for m in msgs]
    assert "STATUSTEXT" in kinds and kinds[-1] == "COMMAND_ACK"
    assert msgs[-1].result == mav.MAV_RESULT_FAILED and not node.streaming
    assert b"camera busy" in msgs[kinds.index("STATUSTEXT")].text.encode() if isinstance(
        msgs[kinds.index("STATUSTEXT")].text, str) else msgs[kinds.index("STATUSTEXT")].text


def test_node_forwards_fc_bytes_only_when_a_gcs_is_known():
    clock, link = Clock(), _gcs_link()
    node, sent, _, _ = _node(clock)
    node.forward(b"fc-bytes")
    assert sent == []
    node.receive(_gcs_heartbeat(link), ("g", 2))
    node.forward(b"fc-bytes")
    assert sent[-1] == (b"fc-bytes", ("g", 2))


def test_unknown_command_is_answered_unsupported():
    clock, link = Clock(), _gcs_link()
    node, sent, starts, _ = _node(clock)
    node.receive(_command(link, mav.MAV_CMD_IMAGE_START_CAPTURE), ("g", 2))
    assert _parse_sent(sent, link)[-1].result == mav.MAV_RESULT_UNSUPPORTED and starts == []


# ---- fc_bridge ----

class FakePort:
    def __init__(self, data: bytes, chunk: int) -> None:
        self._data, self._chunk = data, chunk

    def read(self, size: int) -> bytes:
        out, self._data = self._data[:self._chunk], self._data[self._chunk:]
        return out


def test_fc_bridge_forwards_whole_messages_even_when_split():
    fc = mav.MAVLink(None, srcSystem=1, srcComponent=1)
    gps = fc.gps_raw_int_encode(0, 3, 1, 2, 3, 0, 0, 0, 0, 7).pack(fc)
    att = fc.attitude_encode(0, 0.1, 0.2, 0.3, 0, 0, 0).pack(fc)
    forwarded = []
    bridge = FcBridge(FakePort(gps + att, chunk=7), forwarded.append)
    for _ in range(20):
        bridge.pump()
    assert forwarded == [gps, att] and bridge.messages == 2


def test_open_fc_bridge_is_optional():
    assert open_fc_bridge("", 57600, lambda b: None) is None


# ---- 프로그램 조립 ----

def test_stream_and_tcp_programs_import():
    import importlib
    for name in ("stream", "tcp"):
        assert callable(importlib.import_module(f"src.programs.{name}").main)
