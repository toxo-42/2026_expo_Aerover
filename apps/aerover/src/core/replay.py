"""녹화 재생 — 수집 사진 폴더를 파이처럼 영상으로 흘린다.

통신이 끊겼을 때(시연장 Wi-Fi 등) 드론 상태 페이지의 [재생] 이 이걸 앱 안 스레드로 띄우고
`LinkHub` 가 127.0.0.1 로 붙는다. 화면 · 탐지 · 3D 핀은 **실제 링크와 같은 경로**로 돈다.
장비 없이 따로 띄우는 가짜 파이(`tools/fake_pi.py`)도 같은 노드를 쓴다.

파이 `app.py stream` 과 같은 말을 한다 (`pi_code/src/control/camera_node.py`):

    지상국 → 여기   HEARTBEAT · COMMAND_LONG VIDEO_START_STREAMING / VIDEO_STOP_STREAMING
    여기 → 지상국   HEARTBEAT(1Hz, 카메라 컴포넌트) · COMMAND_ACK · RTP/JPEG (지상국 RTP 포트로)

지상국 HEARTBEAT 가 `GCS_TIMEOUT` 보다 오래 끊기면 스트림을 멈추는 것도 같다.
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
from pymavlink.dialects.v20 import common as mav

from src.config import RTP_PORT
from src.core.rtpjpeg import RtpJpegPacketizer

FRAME_SIZE = (1280, 960)        # 스트림 해상도 = 자세 추정 캘리브레이션 해상도 (`wframe.FRAME_SIZE`)
JPEG_QUALITY = 85
HEARTBEAT_SEC = 1.0
GCS_TIMEOUT = 3.0               # 파이 camera_node 기본값과 같다


# ---- 프레임 ----

def list_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))


def encode_frame(path: Path, size: tuple[int, int] = FRAME_SIZE) -> bytes:
    """사진 한 장 → 스트림 해상도 JPEG.

    4000×3000 폰 사진은 그대로 열면 느려서 1/2 로 읽고 줄인다. 그러면 스트림보다 작아지는
    사진(파이 수집본 1280×960 등)은 원본으로 다시 읽는다 — 1/2 로 읽고 키우면 흐려진다.
    이미 스트림 해상도인 JPEG 는 다시 인코딩하지 않고 파일 그대로 보낸다.
    """
    img = cv2.imread(str(path), cv2.IMREAD_REDUCED_COLOR_2)
    if img is not None and img.shape[1] < size[0]:
        img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"사진을 못 읽었다 — {path}")
    h, w = img.shape[:2]
    if (w, h) == size and path.suffix.lower() in (".jpg", ".jpeg"):
        return path.read_bytes()
    if abs(w / h - size[0] / size[1]) > 1e-2:
        raise ValueError(f"종횡비가 다르다 — {path.name} {w}x{h}, 스트림 {size[0]}x{size[1]}")
    img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError(f"JPEG 인코딩 실패 — {path}")
    return buf.tobytes()


# ---- MAVLink 카메라 노드 ----

class FakePiNode:
    """소켓 하나로 MAVLink 를 받고 답하며, 스트리밍 중이면 프레임을 RTP 로 쏜다.

    `step()` 한 번이 수신 하나 · 하트비트 · 프레임 하나를 처리한다. 루프는 `serve` 가 돈다.
    """

    def __init__(self, sock: socket.socket, images: list[Path], fps: float,
                 rtp_port: int = RTP_PORT, loop: bool = True,
                 log: Callable[[str], None] = print) -> None:
        self.sock = sock
        self.images, self.fps, self.rtp_port, self.loop = images, fps, rtp_port, loop
        self.log = log
        self._mav = mav.MAVLink(None, srcSystem=1, srcComponent=mav.MAV_COMP_ID_CAMERA)
        self._mav.robust_parsing = True
        self._rtp = RtpJpegPacketizer()
        self.gcs: tuple[str, int] | None = None     # 마지막 HEARTBEAT 를 보낸 지상국
        self.last_gcs = 0.0
        self.streaming = False
        self.index = 0                              # 다음에 보낼 사진
        self._next_hb = 0.0
        self._next_frame = 0.0

    def step(self, now: float) -> None:
        self._receive(now)
        if self.gcs is not None and now >= self._next_hb:
            self._send(self._mav.heartbeat_encode(mav.MAV_TYPE_CAMERA, mav.MAV_AUTOPILOT_INVALID, 0, 0, 4))
            self._next_hb = now + HEARTBEAT_SEC
        if self.streaming and now - self.last_gcs > GCS_TIMEOUT:
            self.streaming = False
            self.log(f"지상국 HEARTBEAT 가 {GCS_TIMEOUT:.0f}초 없다 — 스트림 정지")
        if self.streaming and now >= self._next_frame:
            self._send_frame()
            self._next_frame = now + 1.0 / self.fps

    def _receive(self, now: float) -> None:
        try:
            data, addr = self.sock.recvfrom(4096)
        except (TimeoutError, BlockingIOError):
            return
        except OSError:                             # 지상국이 꺼져 ICMP 도달 불가가 돌아온 것
            return
        for m in self._mav.parse_buffer(data) or []:
            kind = m.get_type()
            if kind == "HEARTBEAT":
                if self.gcs != addr:
                    self.log(f"지상국 {addr[0]}:{addr[1]}")
                self.gcs, self.last_gcs = addr, now
            elif kind == "COMMAND_LONG" and m.command == mav.MAV_CMD_VIDEO_START_STREAMING:
                self.gcs, self.last_gcs = addr, now
                self._send(self._mav.command_ack_encode(m.command, mav.MAV_RESULT_ACCEPTED))
                if not self.streaming:
                    self.log(f"스트림 시작 — {self.fps:g} fps, {addr[0]}:{self.rtp_port}")
                self.streaming = True
            elif kind == "COMMAND_LONG" and m.command == mav.MAV_CMD_VIDEO_STOP_STREAMING:
                self._send(self._mav.command_ack_encode(m.command, mav.MAV_RESULT_ACCEPTED))
                if self.streaming:
                    self.log("스트림 정지 — 지상국 요청")
                self.streaming = False

    def _send(self, msg) -> None:
        if self.gcs is not None:
            self.sock.sendto(msg.pack(self._mav), self.gcs)

    def _send_frame(self) -> None:
        if self.index >= len(self.images):
            if not self.loop:
                return
            self.index = 0
        path = self.images[self.index]
        self.index += 1
        for p in self._rtp.packets(encode_frame(path)):
            self.sock.sendto(p, (self.gcs[0], self.rtp_port))


def serve(node: FakePiNode, poll: float = 0.005, stop: threading.Event | None = None) -> None:
    node.sock.settimeout(poll)
    while stop is None or not stop.is_set():
        node.step(time.monotonic())


# ---- 앱 안에서 띄우기 ----

class ReplayServer:
    """앱 안의 가짜 파이. `start()` 가 127.0.0.1 의 빈 포트를 잡아 돌려준다 — 그 포트로 연결한다.

    포트를 OS 가 고르게 해서 실제 파이 포트(14550)나 따로 띄운 `tools/fake_pi` 와 부딪히지 않는다.
    """

    def __init__(self, folder: Path, fps: float, rtp_port: int = RTP_PORT) -> None:
        self.folder, self.fps, self.rtp_port = folder, fps, rtp_port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    def start(self) -> int:
        """사진이 없거나 못 읽으면 ValueError — 띄우기 전에 알린다."""
        if not self.folder.is_dir():
            raise ValueError(f"재생 폴더가 없다 — {self.folder}")
        images = list_images(self.folder)
        if not images:
            raise ValueError(f"재생할 사진이 없다 — {self.folder}")
        encode_frame(images[0])                     # 크기 · 종횡비를 먼저 확인한다

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        if hasattr(socket, "SIO_UDP_CONNRESET"):    # 윈도우: 지상국이 먼저 닫혀도 recv 가 오류를 내지 않게
            sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        node = FakePiNode(sock, images, self.fps, self.rtp_port, log=lambda _: None)
        self._sock = sock
        self._thread = threading.Thread(target=serve, args=(node,), kwargs={"stop": self._stop},
                                        name="replay", daemon=True)
        self._thread.start()
        return sock.getsockname()[1]

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
