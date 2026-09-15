"""옛 TCP JPEG 스트림 서버  (원본 cam_server.py 대체). 지상국은 `AEROVER_LINK=tcp` 로 받는다.

    EXP=2500 GAIN=8 python app.py tcp   # 노출 고정 (실내 기준값)
    python app.py tcp                   # 자동 노출
"""
from picamera2.encoders import JpegEncoder

from src.config import STREAM_CLIENT_TIMEOUT_SEC, STREAM_PORT
from src.programs.stream import open_camera
from src.stream.tcp_server import TcpStreamServer


def main() -> None:
    TcpStreamServer(STREAM_PORT, open_camera, JpegEncoder,
                    client_timeout=STREAM_CLIENT_TIMEOUT_SEC).serve_forever()
