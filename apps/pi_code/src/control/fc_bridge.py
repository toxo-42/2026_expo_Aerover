"""FC 의 MAVLink UART → 메시지 단위 바이트. 파이는 내용을 보지 않고 지상국에 중계만 한다.

INAV 는 UART 에 MAVLink 텔레메트리를 **내보내기만** 한다 (transmit-only). 그래서 읽기만 한다.
파싱은 메시지 경계를 맞추기 위해서다 — UDP 데이터그램 하나에 메시지 하나를 담아야
지상국이 조각을 잇지 않아도 된다. 배선·서비스 설정은 README "파이 ↔ FC" 참고
(2026-09-20 부터 `DRONECAM_FC_SERIAL=/dev/serial0` 으로 중계 중).
"""
from __future__ import annotations

from typing import Callable, Protocol

from pymavlink.dialects.v20 import common as mav

READ_CHUNK = 512


class BytePort(Protocol):
    def read(self, size: int) -> bytes: ...


class FcBridge:
    def __init__(self, port: BytePort, forward: Callable[[bytes], None]) -> None:
        self._port = port
        self._forward = forward
        self._parser = mav.MAVLink(None)
        self._parser.robust_parsing = True
        self.messages = 0

    def pump(self) -> int:
        """포트에 쌓인 바이트를 읽어 완성된 메시지마다 forward 한다. 넘긴 메시지 수를 돌려준다."""
        data = self._port.read(READ_CHUNK)
        if not data:
            return 0
        n = 0
        for m in self._parser.parse_buffer(data) or []:
            self._forward(m.get_msgbuf())
            n += 1
        self.messages += n
        return n


def open_fc_bridge(device: str, baud: int, forward: Callable[[bytes], None]) -> FcBridge | None:
    """설정에 장치가 있으면 시리얼을 열어 브리지를 만든다. 없으면 None (중계 안 함)."""
    if not device:
        return None
    import serial                       # 지연 import — pyserial 은 중계할 때만 필요하다
    return FcBridge(serial.Serial(device, baud, timeout=0), forward)
