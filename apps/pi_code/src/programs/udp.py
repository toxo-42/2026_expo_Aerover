"""인터벌 캡처 → JPEG → UDP 청크 전송  (원본 imagesend.py 대체)

    DRONECAM_DEST_IP=192.168.137.1 python app.py udp
"""
import socket
import time

from src.camera.factory import open_still_camera
from src.camera.settings import StillCameraSettings
from src.config import DEST_IP, DEST_PORT, INTERVAL_SEC, JPG_QUALITY, NUM_SHOTS
from src.log import log
from src.stream.jpeg import encode_bgr_as_jpeg
from src.stream.udp_sender import IntervalSender, UdpChunkSender


def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    settings = StillCameraSettings()
    picam2 = open_still_camera(settings)
    picam2.start()
    time.sleep(settings.warmup_sec)

    log(f"전송 시작 → {DEST_IP}:{DEST_PORT}")
    job = IntervalSender(capture=picam2.capture_array,
                         encode=lambda frame: encode_bgr_as_jpeg(frame, JPG_QUALITY),
                         sender=UdpChunkSender(sock, (DEST_IP, DEST_PORT)),
                         interval_sec=INTERVAL_SEC,
                         num_shots=NUM_SHOTS,
                         on_sent=lambda i, n, c: log(f"[img {i}] {n} bytes, {c} chunks 전송"))
    try:
        job.run()
    except KeyboardInterrupt:
        log("사용자 중단")
    finally:
        picam2.stop()
        picam2.close()
        sock.close()
        log(f"완료: 총 {job.sent}장 전송")
