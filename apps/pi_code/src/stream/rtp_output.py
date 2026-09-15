from __future__ import annotations

import io
import socket
from typing import Callable

from src.stream.rtpjpeg import RtpJpegPacketizer, UnsupportedJpeg


class RtpJpegOutput(io.BufferedIOBase):
    """FileOutput 에 꽂는 어댑터. 인코더가 JPEG 한 장을 write() 할 때마다 RTP 패킷으로 쪼개 UDP 로 보낸다.

    UDP 라 실패해도 재전송하지 않는다 — 세기만 하고 다음 프레임을 보낸다.
    RFC 2435 에 실을 수 없는 JPEG(예: 최적화 허프만 테이블)은 `on_error` 로 알리고 버린다.
    """

    def __init__(self, sock: socket.socket, dest: tuple[str, int],
                 packetizer: RtpJpegPacketizer | None = None,
                 on_error: Callable[[Exception], None] | None = None) -> None:
        self.sock = sock
        self.dest = dest
        self.packetizer = packetizer or RtpJpegPacketizer()
        self.on_error = on_error
        self.frames = 0
        self.packets = 0
        self.errors = 0

    def write(self, buf) -> int:
        try:
            packets = self.packetizer.packets(bytes(buf))
        except UnsupportedJpeg as e:
            self._error(e)
            return 0
        for packet in packets:
            try:
                self.sock.sendto(packet, self.dest)
            except OSError as e:
                self._error(e)
                return 0
            self.packets += 1
        self.frames += 1
        return len(buf)

    def _error(self, e: Exception) -> None:
        self.errors += 1
        if self.on_error:
            self.on_error(e)
