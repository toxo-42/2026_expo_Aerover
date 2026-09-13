"""지상국 1대를 받는 TCP 스트림 서버.

접속마다 카메라를 열고, 끊기면 카메라를 해제한다. 카메라를 어떻게 여는지·어떤
인코더를 쓰는지는 주입받는다 — 이 클래스는 **연결의 수명**만 책임진다.

클라이언트 소켓에는 송신 타임아웃과 keepalive 를 건다. 없으면 Wi-Fi 가 끊겼을 때
`sendall` 이 TCP 재전송 만료(수 분)까지 막혀 다음 접속을 못 받는다.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Callable

from picamera2 import Picamera2
from picamera2.encoders import Encoder
from picamera2.outputs import FileOutput

from src.log import log
from src.stream.socket_output import SocketFrameOutput

POLL_SEC = 0.1      # 연결이 살아 있는지 확인하는 주기


class TcpStreamServer:
    def __init__(self, port: int,
                 open_camera: Callable[[], Picamera2],
                 make_encoder: Callable[[], Encoder],
                 client_timeout: float = 5.0,
                 bind_host: str = "0.0.0.0") -> None:
        self.port = port
        self.bind_host = bind_host
        self.client_timeout = client_timeout
        self._open_camera = open_camera
        self._make_encoder = make_encoder

    def serve_forever(self) -> None:
        with self._listen() as srv:
            while True:
                conn, addr = srv.accept()
                log(f"접속: {addr}")
                self.serve_client(conn)

    def _listen(self) -> socket.socket:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.bind_host, self.port))
        srv.listen(1)
        log(f"대기 중... (포트 {self.port})")
        return srv

    def serve_client(self, conn: socket.socket) -> None:
        """연결 하나의 수명 — 카메라를 열고, 끊길 때까지 보내고, 정리한다."""
        self._harden(conn)
        picam2: Picamera2 | None = None
        try:
            picam2 = self._open_camera()
            alive = threading.Event()
            alive.set()
            picam2.start_recording(self._make_encoder(),
                                   FileOutput(SocketFrameOutput(conn, alive)))
            while alive.is_set():
                time.sleep(POLL_SEC)
        except Exception as e:
            log(f"오류: {e}")
        finally:
            self._release(picam2)
            try:
                conn.close()
            except OSError:
                pass
            log("연결 종료, 대기 중...")

    def _harden(self, conn: socket.socket) -> None:
        conn.settimeout(self.client_timeout)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    @staticmethod
    def _release(picam2: Picamera2 | None) -> None:
        if picam2 is None:
            return
        try:
            picam2.stop_recording()
        except Exception:
            pass
        picam2.close()
        log("카메라 해제")
