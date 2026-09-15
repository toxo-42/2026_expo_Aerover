"""stream 패키지 — framing · socket_output · tcp_server · mjpeg · jpeg · udp_sender."""
import socket
import struct
import threading
import time

import pytest
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder

from src.stream import framing
from src.stream.jpeg import encode_bgr_as_jpeg
from src.stream.mjpeg import BOUNDARY, FrameBroadcaster, multipart_frames
from src.stream.socket_output import SocketFrameOutput
from src.stream.tcp_server import TcpStreamServer
from src.stream.udp_sender import IntervalSender, UdpChunkSender


# ---- framing ----

def test_length_prefixed_is_big_endian_u32():
    assert framing.length_prefixed(b"abc") == b"\x00\x00\x00\x03abc"


def test_udp_chunks_cover_the_payload_exactly():
    data = bytes(range(256)) * 10          # 2560 바이트
    packets = list(framing.udp_chunks(7, data, chunk_size=1000))
    assert len(packets) == framing.chunk_count(len(data), 1000) == 3
    headers = [struct.unpack(framing.UDP_HEADER_FMT, p[:framing.UDP_HEADER_SIZE]) for p in packets]
    assert headers == [(7, 3, 0), (7, 3, 1), (7, 3, 2)]
    assert b"".join(p[framing.UDP_HEADER_SIZE:] for p in packets) == data


# ---- socket_output ----

def _read_frame(sock: socket.socket) -> bytes:
    header = b""
    while len(header) < 4:
        header += sock.recv(4 - len(header))
    (n,) = struct.unpack(framing.LENGTH_FMT, header)
    body = b""
    while len(body) < n:
        body += sock.recv(n - len(body))
    return body


def test_socket_output_frames_and_lowers_alive_on_failure():
    a, b = socket.socketpair()
    alive = threading.Event()
    alive.set()
    out = SocketFrameOutput(a, alive)
    assert out.write(b"hello") == 5
    assert _read_frame(b) == b"hello"

    b.close()
    for _ in range(50):                     # 상대가 닫힌 뒤 보내면 결국 실패한다
        out.write(b"x" * 65536)
        if not alive.is_set():
            break
    assert not alive.is_set()
    assert out.write(b"more") == 0          # 끊긴 뒤에는 보내지 않는다
    a.close()


# ---- tcp_server ----

def test_tcp_server_serves_one_client_then_releases_camera():
    from src.camera.factory import open_stream_camera
    from src.camera.settings import StreamCameraSettings

    Picamera2.instances.clear()
    server_side, client = socket.socketpair()
    server = TcpStreamServer(0, open_camera=lambda: open_stream_camera(StreamCameraSettings(), {}),
                             make_encoder=JpegEncoder, client_timeout=1.0)

    t = threading.Thread(target=server.serve_client, args=(server_side,), daemon=True)
    t.start()

    frames = [_read_frame(client) for _ in range(3)]
    assert frames == [JpegEncoder.frame] * 3
    client.close()                          # 지상국이 끊는다
    t.join(5.0)
    assert not t.is_alive()

    cam = Picamera2.instances[-1]
    assert cam.closed and cam.recording is None
    assert cam.config["kind"] == "video"


def test_tcp_server_closes_camera_when_configure_fails():
    Picamera2.instances.clear()
    server_side, client = socket.socketpair()

    def broken_camera():
        raise RuntimeError("no camera")

    TcpStreamServer(0, open_camera=broken_camera, make_encoder=JpegEncoder).serve_client(server_side)
    assert client.recv(1) == b""            # 서버가 소켓을 닫았다
    client.close()


# ---- mjpeg ----

def test_broadcaster_hands_latest_frame_to_waiters():
    bc = FrameBroadcaster()
    got = []
    t = threading.Thread(target=lambda: got.append(bc.wait_frame(timeout=2.0)))
    t.start()
    time.sleep(0.05)
    bc.write(bytearray(b"jpeg-1"))          # 인코더는 재사용 버퍼를 준다 — 복사해야 한다
    t.join(2.0)
    assert got == [b"jpeg-1"]
    assert bc.wait_frame(timeout=0.01) is None


def test_multipart_body_format():
    bc = FrameBroadcaster()
    gen = multipart_frames(bc)
    threading.Timer(0.05, lambda: bc.write(b"IMG")).start()
    part = next(gen)
    assert part.startswith(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: 3\r\n\r\n".encode())
    assert part.endswith(b"IMG\r\n")


# ---- jpeg ----

def test_encode_flips_bgr_to_rgb():
    from PIL import Image
    import io
    frame = Picamera2().capture_array()     # 0번 채널만 255 — picamera2 배열은 실제로 BGR 순서다
    data = encode_bgr_as_jpeg(frame, quality=95)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    r, g, b = img.getpixel((0, 0))
    assert b > 200 and r < 50               # 배열의 0번 채널(B)이 이미지에서도 B 로 간다


# ---- udp_sender ----

class FakeSock:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple]] = []

    def sendto(self, packet: bytes, dest: tuple) -> None:
        self.sent.append((packet, dest))


def test_udp_chunk_sender_and_interval_loop():
    sock = FakeSock()
    sender = UdpChunkSender(sock, ("10.0.0.1", 9999), chunk_size=4, sleep=lambda s: None)
    assert sender.send(3, b"0123456789") == 3
    assert [d for _, d in sock.sent] == [("10.0.0.1", 9999)] * 3

    events = []
    job = IntervalSender(capture=lambda: "frame", encode=lambda f: b"AB" * 3, sender=sender,
                         interval_sec=0.0, num_shots=2, on_sent=lambda *e: events.append(e),
                         sleep=lambda s: None)
    job.run()
    assert job.sent == 2
    assert events == [(0, 6, 2), (1, 6, 2)]


def test_interval_sender_stops_on_keyboard_interrupt_from_caller():
    def capture():
        raise KeyboardInterrupt
    job = IntervalSender(capture, lambda f: b"", UdpChunkSender(FakeSock(), ("h", 1)), 0.0, None)
    with pytest.raises(KeyboardInterrupt):    # 처리는 프로그램(programs/udp.py)이 한다
        job.run()
