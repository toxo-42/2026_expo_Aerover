"""파이 ↔ 지상국 전송 규약 — **길이 4바이트(!I, big-endian) + JPEG 바이트** 의 반복.

보내는 쪽은 `pi_code/src/stream/framing.py`. 두 파일의 `HEADER` 가 같아야 한다.
소켓·스레드·Qt 를 모르는 순수 로직이라 바이트만으로 테스트한다.
"""
from __future__ import annotations

import struct
from typing import Callable

HEADER = struct.Struct("!I")
MAX_FRAME = 32 * 1024 * 1024        # 이보다 큰 길이는 깨진 헤더로 본다

# recv(n) 은 1~n 바이트를 돌려준다. 빈 bytes 나 None 은 "닫힘 또는 중단" 이다.
Recv = Callable[[int], bytes | None]


def length_prefixed(payload: bytes) -> bytes:
    return HEADER.pack(len(payload)) + payload


class FrameReader:
    """recv 함수 하나로 프레임을 하나씩 읽는다."""

    def __init__(self, recv: Recv, max_frame: int = MAX_FRAME) -> None:
        self._recv = recv
        self._max_frame = max_frame

    def read(self) -> bytes | None:
        """프레임 하나. 닫히거나 중단되면 None."""
        header = self._exact(HEADER.size)
        if header is None:
            return None
        (n,) = HEADER.unpack(header)
        if n > self._max_frame:
            raise ValueError(f"프레임 길이가 비정상이다: {n} 바이트")
        return self._exact(n)

    def _exact(self, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)
