from __future__ import annotations

import socket
import time
from typing import Callable

from src.stream.framing import udp_chunks

CHUNK_SIZE = 60000          # UDP 페이로드 64KB 미만
PAUSE_SEC = 0.001           # 청크 사이 쉬는 시간 — 순간 폭주로 인한 유실 완화

OnSent = Callable[[int, int, int], None]      # (이미지 번호, 바이트 수, 청크 수)


class UdpChunkSender:
    """이미지 한 장을 UDP 청크로 쪼개 보낸다. 무엇을 찍을지는 모른다."""

    def __init__(self, sock: socket.socket, dest: tuple[str, int],
                 chunk_size: int = CHUNK_SIZE, pause_sec: float = PAUSE_SEC,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.sock = sock
        self.dest = dest
        self.chunk_size = chunk_size
        self.pause_sec = pause_sec
        self._sleep = sleep

    def send(self, img_id: int, data: bytes) -> int:
        """보낸 청크 수를 돌려준다."""
        total = 0
        for packet in udp_chunks(img_id, data, self.chunk_size):
            self.sock.sendto(packet, self.dest)
            self._sleep(self.pause_sec)
            total += 1
        return total


class IntervalSender:
    """일정 간격으로 캡처 → 인코딩 → 전송. 세 단계 모두 주입받는다.

    출력은 하지 않는다 — 한 장 보낼 때마다 `on_sent` 를 부를 뿐이다.
    """

    def __init__(self, capture: Callable[[], object],
                 encode: Callable[[object], bytes],
                 sender: UdpChunkSender,
                 interval_sec: float,
                 num_shots: int | None,                    # None 이면 무한
                 on_sent: OnSent | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.capture = capture
        self.encode = encode
        self.sender = sender
        self.interval_sec = interval_sec
        self.num_shots = num_shots
        self.on_sent = on_sent
        self._sleep = sleep
        self.sent = 0

    def run(self) -> None:
        while self.num_shots is None or self.sent < self.num_shots:
            data = self.encode(self.capture())
            chunks = self.sender.send(self.sent, data)
            if self.on_sent:
                self.on_sent(self.sent, len(data), chunks)
            self.sent += 1
            self._sleep(self.interval_sec)
