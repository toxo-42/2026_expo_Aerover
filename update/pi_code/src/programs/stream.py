"""지상국(aerover) 링크 — UDP. MAVLink 로 제어받고 RTP/JPEG 로 영상을 보낸다.

    python app.py stream                                   # 자동 노출
    EXP=2500 GAIN=8 python app.py stream                   # 노출 고정 (실내 기준값)
    DRONECAM_FC_SERIAL=/dev/serial0 python app.py stream   # FC 의 MAVLink 텔레메트리도 중계

동작: 지상국이 HEARTBEAT 와 VIDEO_START_STREAMING 을 보내면 카메라를 열어 그 주소로 RTP 를
보낸다. VIDEO_STOP_STREAMING 이 오거나 지상국 HEARTBEAT 가 끊기면 카메라를 놓는다.
옛 TCP 방식은 `python app.py tcp`.
"""
import select
import socket

from picamera2.encoders import JpegEncoder

from src.camera.factory import open_stream_camera
from src.camera.settings import StreamCameraSettings, exposure_controls_from_env
from src.config import (FC_BAUD, FC_SERIAL, GCS_TIMEOUT_SEC, MAVLINK_PORT, RTP_MTU,
                        RTP_PORT)
from src.control.camera_node import CameraNode
from src.control.fc_bridge import open_fc_bridge
from src.log import log
from src.stream.rtp_output import RtpJpegOutput
from src.stream.rtp_session import RtpStreamSession
from src.stream.rtpjpeg import RtpJpegPacketizer

POLL_SEC = 0.05


def open_camera():
    controls = exposure_controls_from_env()
    if controls:
        log(f"노출 고정: {controls['ExposureTime']}us, gain {controls['AnalogueGain']}")
    return open_stream_camera(StreamCameraSettings(), controls)


def main() -> None:
    mav_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mav_sock.bind(("0.0.0.0", MAVLINK_PORT))
    video_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    session = RtpStreamSession(
        open_camera, JpegEncoder,
        make_output=lambda dest: RtpJpegOutput(video_sock, dest, RtpJpegPacketizer(mtu=RTP_MTU),
                                               on_error=lambda e: log(f"RTP 오류: {e}")))
    def send(data: bytes, addr: tuple[str, int]) -> None:
        try:
            mav_sock.sendto(data, addr)
        except OSError:                         # UDP — 망이 잠깐 없어도 다음 HEARTBEAT 에 다시 시도한다
            pass

    node = CameraNode(send=send,
                      on_start=session.start, on_stop=session.stop,
                      rtp_port=RTP_PORT, gcs_timeout=GCS_TIMEOUT_SEC)
    bridge = open_fc_bridge(FC_SERIAL, FC_BAUD, node.forward)

    log(f"MAVLink 대기 중 (UDP {MAVLINK_PORT}) — 영상은 지상국 {RTP_PORT} 으로"
        + (f", FC 중계 {FC_SERIAL}" if bridge else ""))
    try:
        while True:
            readable, _, _ = select.select([mav_sock], [], [], POLL_SEC)
            if readable:
                data, addr = mav_sock.recvfrom(4096)
                node.receive(data, addr)
            node.tick()
            if bridge is not None:
                bridge.pump()
    except KeyboardInterrupt:
        log("사용자 중단")
    finally:
        session.stop()
        mav_sock.close()
        video_sock.close()
        log("종료")
