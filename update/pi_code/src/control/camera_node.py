"""파이 쪽 MAVLink 카메라 노드 — 지상국의 HEARTBEAT 와 스트리밍 명령을 받는다.

    지상국 → 파이   HEARTBEAT · COMMAND_LONG VIDEO_START_STREAMING / VIDEO_STOP_STREAMING
    파이 → 지상국   HEARTBEAT(1Hz, 카메라 컴포넌트) · COMMAND_ACK · STATUSTEXT(오류)
                   + `forward()` 로 넘어온 FC 텔레메트리 바이트를 그대로 중계

스트리밍 대상은 **마지막 HEARTBEAT 를 보낸 지상국 주소**다. 그 HEARTBEAT 가 `gcs_timeout` 보다
오래 끊기면 스트림을 멈춘다 — TCP 때처럼 죽은 연결을 붙들고 있지 않는다.
소켓은 모른다. 보낼 바이트와 주소를 `send` 로 넘긴다. 지상국 쪽 짝은 `aerover/src/core/gcs.py`.
"""
from __future__ import annotations

import time
from typing import Callable

from pymavlink.dialects.v20 import common as mav

from src.log import log

Address = tuple[str, int]
Send = Callable[[bytes, Address], None]

VEHICLE_SYSTEM_ID = 1
CAMERA_COMPONENT_ID = mav.MAV_COMP_ID_CAMERA
HEARTBEAT_SEC = 1.0

CMD_VIDEO_START = mav.MAV_CMD_VIDEO_START_STREAMING
CMD_VIDEO_STOP = mav.MAV_CMD_VIDEO_STOP_STREAMING


class CameraNode:
    def __init__(self, send: Send,
                 on_start: Callable[[Address], None],
                 on_stop: Callable[[], None],
                 rtp_port: int,
                 gcs_timeout: float = 3.0,
                 system_id: int = VEHICLE_SYSTEM_ID, component_id: int = CAMERA_COMPONENT_ID,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._mav = mav.MAVLink(None, srcSystem=system_id, srcComponent=component_id)
        self._mav.robust_parsing = True
        self._send = send
        self._on_start = on_start
        self._on_stop = on_stop
        self.rtp_port = rtp_port
        self.gcs_timeout = gcs_timeout
        self._component_id = component_id
        self._clock = clock
        self.gcs: Address | None = None             # 마지막 HEARTBEAT 를 보낸 지상국
        self.last_gcs_heartbeat: float | None = None
        self.streaming = False
        self._next_heartbeat = 0.0

    # ---- 받기 ----

    def receive(self, data: bytes, addr: Address) -> None:
        for m in self._mav.parse_buffer(data) or []:
            kind = m.get_type()
            if kind == "HEARTBEAT":
                self._on_gcs_heartbeat(addr)
            elif kind == "COMMAND_LONG" and m.target_component in (0, self._component_id):
                self._on_command(m, addr)

    def _on_gcs_heartbeat(self, addr: Address) -> None:
        if self.gcs != addr:
            log(f"지상국 {addr[0]}:{addr[1]}")
        self.gcs = addr
        self.last_gcs_heartbeat = self._clock()

    def _on_command(self, m, addr: Address) -> None:
        if m.command == CMD_VIDEO_START:
            self._on_gcs_heartbeat(addr)                # 명령도 살아 있다는 신호다
            result = self._try(lambda: self._on_start((addr[0], self.rtp_port)), "스트림 시작")
            self.streaming = result == mav.MAV_RESULT_ACCEPTED
        elif m.command == CMD_VIDEO_STOP:
            result = self._try(self._on_stop, "스트림 정지")
            self.streaming = False
        else:
            result = mav.MAV_RESULT_UNSUPPORTED
        ack = self._mav.command_ack_encode(m.command, result, 0, 0,
                                           m.get_srcSystem(), m.get_srcComponent())
        self._send(ack.pack(self._mav), addr)

    def _try(self, action: Callable[[], None], what: str) -> int:
        try:
            action()
        except Exception as e:                          # 카메라를 못 열었다 등 — 지상국에 알린다
            log(f"{what} 실패: {e}")
            self.status_text(f"{what} 실패: {e}")
            return mav.MAV_RESULT_FAILED
        return mav.MAV_RESULT_ACCEPTED

    # ---- 주기 ----

    def tick(self) -> None:
        """자주 부른다 — 1초마다 HEARTBEAT, 지상국이 사라지면 스트림 정지."""
        if self.gcs is None:
            return
        now = self._clock()
        if now >= self._next_heartbeat:
            hb = self._mav.heartbeat_encode(mav.MAV_TYPE_CAMERA, mav.MAV_AUTOPILOT_INVALID,
                                            0, 0, mav.MAV_STATE_ACTIVE)
            self._send(hb.pack(self._mav), self.gcs)
            self._next_heartbeat = now + HEARTBEAT_SEC
        if self.streaming and now - self.last_gcs_heartbeat > self.gcs_timeout:
            log(f"지상국 HEARTBEAT {self.gcs_timeout:.0f}초 없음 — 스트림 정지")
            self._try(self._on_stop, "스트림 정지")
            self.streaming = False
            self.gcs = None

    # ---- 보내기 ----

    def forward(self, data: bytes) -> None:
        """FC 텔레메트리(MAVLink 원문)를 지상국에 그대로 넘긴다."""
        if self.gcs is not None:
            self._send(data, self.gcs)

    def status_text(self, text: str, severity: int = mav.MAV_SEVERITY_ERROR) -> None:
        if self.gcs is None:
            return
        msg = self._mav.statustext_encode(severity, text.encode("utf-8")[:50])
        self._send(msg.pack(self._mav), self.gcs)
