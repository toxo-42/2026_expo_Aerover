from __future__ import annotations

import io
import socket
import threading

from src.stream.framing import length_prefixed


class SocketFrameOutput(io.BufferedIOBase):
    """FileOutput 에 꽂는 어댑터. 인코더가 JPEG 한 장을 write() 할 때마다 길이 헤더를 붙여 보낸다.

    전송이 실패하면(끊김·타임아웃) alive 를 내려서, 서버 루프가 연결을 정리하게 한다.
    """

    def __init__(self, conn: socket.socket, alive: threading.Event) -> None:
        self.conn = conn
        self.alive = alive

    def write(self, buf) -> int:
        if not self.alive.is_set():
            return 0                    # 이미 끊겼다 — 더 보내려 하지 않는다
        try:
            self.conn.sendall(length_prefixed(bytes(buf)))
        except OSError:                 # BrokenPipe · ConnectionReset · timeout 전부 여기
            self.alive.clear()
            return 0
        return len(buf)
