"""가짜 파이 — 장비 없이 사진 폴더를 드론 영상처럼 재생한다.

    uv run python -m tools.fake_pi sessions/<회차> --odm-camera 3D_model/<회차>/cameras.json
    AEROVER_PI_HOST=127.0.0.1 AEROVER_CAMERA=3D_model/<회차>/replay_camera.json uv run python app.py

앱은 **고치지 않고** 그대로 붙는다 — 드론 상태 페이지에서 127.0.0.1:14550 으로 연결하면 된다.
파이 `app.py stream` 과 같은 말을 한다 (`pi_code/src/control/camera_node.py`):

    지상국 → 여기   HEARTBEAT · COMMAND_LONG VIDEO_START_STREAMING / VIDEO_STOP_STREAMING
    여기 → 지상국   HEARTBEAT(1Hz, 카메라 컴포넌트) · COMMAND_ACK · RTP/JPEG (지상국 RTP 포트로)

지상국 HEARTBEAT 가 `GCS_TIMEOUT` 보다 오래 끊기면 스트림을 멈추는 것도 같다.

**카메라 K 를 바꿔 끼워야 한다.** 수집 사진은 폰으로 찍어서 파이캠 K 로 자세를 풀면 재투영 오차가
14px 까지 난다. `--odm-camera` 를 주면 ODM 이 번들조정으로 구한 그 폰의 값을 1280×960 용
K · 왜곡으로 바꿔 `replay_camera.json` 으로 저장한다 — 앱은 `AEROVER_CAMERA` 로 그것을 읽는다.

재생으로 **못 보는 것**: 파이캠 렌즈의 강한 왜곡과 화질, 실제 Wi-Fi 의 지연 · 패킷 손실.
"""
from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

import cv2
import numpy as np
from pymavlink.dialects.v20 import common as mav

from src.config import MAVLINK_PORT, RTP_PORT
from src.core.rtpjpeg import RtpJpegPacketizer

FRAME_SIZE = (1280, 960)        # 스트림 해상도 = 자세 추정 캘리브레이션 해상도 (`wframe.FRAME_SIZE`)
JPEG_QUALITY = 85
HEARTBEAT_SEC = 1.0
GCS_TIMEOUT = 3.0               # 파이 camera_node 기본값과 같다
CAMERA_OUT = "replay_camera.json"


# ---- 카메라 K (ODM → aruco 형식) ----

def odm_camera(cam: dict, size: tuple[int, int] = FRAME_SIZE) -> tuple[np.ndarray, np.ndarray]:
    """ODM(OpenSfM) `cameras.json` 의 brown 카메라 하나 → 주어진 해상도의 (K, dist).

    OpenSfM 은 초점거리 · 주점을 **긴 변으로 나눈 값**, 주점은 영상 중심 기준으로 적는다.
    해상도를 바꿔도(종횡비가 같으면) 정규화 값은 그대로라 긴 변만 곱하면 된다.
    dist 는 OpenCV 순서 (k1, k2, p1, p2, k3).
    """
    if cam.get("projection_type") != "brown":
        raise ValueError(f"brown 카메라만 바꾼다 — {cam.get('projection_type')}")
    w, h = size
    if abs(cam["width"] / cam["height"] - w / h) > 1e-3:
        raise ValueError(f"종횡비가 다르다 — 사진 {cam['width']}x{cam['height']}, 스트림 {w}x{h}")
    s = max(w, h)
    K = np.array([[cam["focal_x"] * s, 0.0, w / 2 + cam["c_x"] * s],
                  [0.0, cam["focal_y"] * s, h / 2 + cam["c_y"] * s],
                  [0.0, 0.0, 1.0]])
    dist = np.array([cam[k] for k in ("k1", "k2", "p1", "p2", "k3")], float)
    return K, dist


def write_replay_camera(cameras_json: Path, size: tuple[int, int] = FRAME_SIZE) -> Path:
    """ODM cameras.json 옆에 aruco `load_camera` 가 읽는 형식으로 저장한다. 카메라가 여럿이면 거부."""
    cams = json.loads(cameras_json.read_text())
    if len(cams) != 1:
        raise ValueError(f"카메라가 {len(cams)}개 — 하나여야 어느 것인지 안다")
    name, cam = next(iter(cams.items()))
    K, dist = odm_camera(cam, size)
    out = cameras_json.parent / CAMERA_OUT
    out.write_text(json.dumps({"source": f"{cameras_json} — {name}", "image_size": list(size),
                               "K": K.tolist(), "dist": dist.tolist()}, indent=2))
    return out


# ---- 프레임 ----

def list_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))


def encode_frame(path: Path, size: tuple[int, int] = FRAME_SIZE) -> bytes:
    """사진 한 장 → 스트림 해상도 JPEG. 4000×3000 을 그대로 열면 느려서 1/2 로 읽고 줄인다."""
    img = cv2.imread(str(path), cv2.IMREAD_REDUCED_COLOR_2)
    if img is None:
        raise ValueError(f"사진을 못 읽었다 — {path}")
    h, w = img.shape[:2]
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
                 rtp_port: int = RTP_PORT, loop: bool = True) -> None:
        self.sock = sock
        self.images, self.fps, self.rtp_port, self.loop = images, fps, rtp_port, loop
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
            print(f"지상국 HEARTBEAT 가 {GCS_TIMEOUT:.0f}초 없다 — 스트림 정지")
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
                    print(f"지상국 {addr[0]}:{addr[1]}")
                self.gcs, self.last_gcs = addr, now
            elif kind == "COMMAND_LONG" and m.command == mav.MAV_CMD_VIDEO_START_STREAMING:
                self.gcs, self.last_gcs = addr, now
                self._send(self._mav.command_ack_encode(m.command, mav.MAV_RESULT_ACCEPTED))
                if not self.streaming:
                    print(f"스트림 시작 — {self.fps:g} fps, {addr[0]}:{self.rtp_port}")
                self.streaming = True
            elif kind == "COMMAND_LONG" and m.command == mav.MAV_CMD_VIDEO_STOP_STREAMING:
                self._send(self._mav.command_ack_encode(m.command, mav.MAV_RESULT_ACCEPTED))
                if self.streaming:
                    print("스트림 정지 — 지상국 요청")
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


def serve(node: FakePiNode, poll: float = 0.005) -> None:
    node.sock.settimeout(poll)
    while True:
        node.step(time.monotonic())


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="사진 폴더를 드론 영상처럼 재생하는 가짜 파이")
    ap.add_argument("folder", type=Path, help="사진 폴더 (예: sessions/20261004_130739)")
    ap.add_argument("--odm-camera", type=Path,
                    help="ODM cameras.json — 주면 그 옆에 replay_camera.json 을 만든다 (AEROVER_CAMERA 로 쓴다)")
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--step", type=int, default=1, help="N 장마다 한 장 (빨리 훑기)")
    ap.add_argument("--start", type=int, default=0, help="몇 번째 사진부터")
    ap.add_argument("--once", action="store_true", help="끝까지 한 번만 (기본은 되풀이)")
    ap.add_argument("--port", type=int, default=MAVLINK_PORT, help="MAVLink 를 듣는 UDP 포트")
    ap.add_argument("--rtp-port", type=int, default=RTP_PORT, help="지상국이 RTP 를 받는 포트")
    args = ap.parse_args(argv)

    if args.odm_camera is not None:
        out = write_replay_camera(args.odm_camera)
        print(f"카메라 K → {out}\n  앱은 AEROVER_CAMERA={out} 로 켠다")
    images = list_images(args.folder)[args.start::args.step]
    if not images:
        raise SystemExit(f"사진이 없다 — {args.folder}")
    encode_frame(images[0])                         # 크기 · 종횡비를 먼저 확인한다

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", args.port))
    print(f"사진 {len(images)}장 · MAVLink 127.0.0.1:{args.port} 대기 — 앱에서 127.0.0.1:{args.port} 로 연결")
    try:
        serve(FakePiNode(sock, images, args.fps, args.rtp_port, loop=not args.once))
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
