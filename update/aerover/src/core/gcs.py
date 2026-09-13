"""지상국 MAVLink 끝점 — 파이의 카메라 노드와 UDP 로 이야기한다.

    지상국 → 파이   HEARTBEAT(1Hz) · COMMAND_LONG VIDEO_START_STREAMING / VIDEO_STOP_STREAMING
    파이 → 지상국   HEARTBEAT(카메라 컴포넌트) · COMMAND_ACK · STATUSTEXT(오류)
                   + FC 가 있으면 GPS_RAW_INT · ATTITUDE · SYS_STATUS … (파이가 그대로 중계)

소켓은 모른다 — 보낼 바이트를 `send` 로 넘기고, 받은 바이트를 `receive` 에 넣는다.
파이 쪽 짝은 `pi_code/src/control/camera_node.py`.
"""
from __future__ import annotations

import time
from typing import Callable

from pymavlink.dialects.v20 import common as mav

GCS_SYSTEM_ID = 255
GCS_COMPONENT_ID = mav.MAV_COMP_ID_MISSIONPLANNER
VEHICLE_SYSTEM_ID = 1
CAMERA_COMPONENT_ID = mav.MAV_COMP_ID_CAMERA
HEARTBEAT_SEC = 1.0

CMD_VIDEO_START = mav.MAV_CMD_VIDEO_START_STREAMING
CMD_VIDEO_STOP = mav.MAV_CMD_VIDEO_STOP_STREAMING
RESULT_ACCEPTED = mav.MAV_RESULT_ACCEPTED

RESULT_TEXT = {
    mav.MAV_RESULT_ACCEPTED: "수락",
    mav.MAV_RESULT_TEMPORARILY_REJECTED: "일시 거부",
    mav.MAV_RESULT_DENIED: "거부",
    mav.MAV_RESULT_UNSUPPORTED: "지원하지 않음",
    mav.MAV_RESULT_FAILED: "실패",
    mav.MAV_RESULT_IN_PROGRESS: "진행 중",
}


class GcsEndpoint:
    def __init__(self, send: Callable[[bytes], None],
                 clock: Callable[[], float] = time.monotonic,
                 system_id: int = GCS_SYSTEM_ID, component_id: int = GCS_COMPONENT_ID,
                 target_system: int = VEHICLE_SYSTEM_ID) -> None:
        self._mav = mav.MAVLink(None, srcSystem=system_id, srcComponent=component_id)
        self._mav.robust_parsing = True
        self._send = send
        self._clock = clock
        self._target = target_system
        self._next_heartbeat = 0.0
        self.last_camera_heartbeat: float | None = None
        self.acks: dict[int, int] = {}              # 명령 → MAV_RESULT
        self.last_error_text: str | None = None     # 파이가 보낸 STATUSTEXT (경고 이상)

    # ---- 보내기 ----

    def heartbeat(self) -> None:
        msg = self._mav.heartbeat_encode(mav.MAV_TYPE_GCS, mav.MAV_AUTOPILOT_INVALID,
                                         0, 0, mav.MAV_STATE_ACTIVE)
        self._send(msg.pack(self._mav))
        self._next_heartbeat = self._clock() + HEARTBEAT_SEC

    def tick(self) -> None:
        """주기 작업 — 1초마다 HEARTBEAT. 자주 불러도 된다."""
        if self._clock() >= self._next_heartbeat:
            self.heartbeat()

    def start_streaming(self, stream_id: int = 1) -> None:
        self._command(CMD_VIDEO_START, stream_id)

    def stop_streaming(self, stream_id: int = 1) -> None:
        self._command(CMD_VIDEO_STOP, stream_id)

    def _command(self, command: int, param1: float = 0.0) -> None:
        msg = self._mav.command_long_encode(self._target, CAMERA_COMPONENT_ID, command, 0,
                                            param1, 0, 0, 0, 0, 0, 0)
        self._send(msg.pack(self._mav))

    # ---- 받기 ----

    def receive(self, data: bytes) -> list:
        """바이트를 메시지로 풀고 HEARTBEAT·ACK·STATUSTEXT 를 기록한다. 메시지 전부를 돌려준다."""
        msgs = self._mav.parse_buffer(data) or []
        for m in msgs:
            kind = m.get_type()
            if kind == "HEARTBEAT" and m.get_srcComponent() == CAMERA_COMPONENT_ID:
                self.last_camera_heartbeat = self._clock()
            elif kind == "COMMAND_ACK":
                self.acks[m.command] = m.result
            elif kind == "STATUSTEXT" and m.severity <= mav.MAV_SEVERITY_WARNING:
                self.last_error_text = _text(m.text)
        return msgs

    @property
    def camera_seen(self) -> bool:
        return self.last_camera_heartbeat is not None

    def camera_alive(self, timeout: float) -> bool:
        return self.camera_seen and self._clock() - self.last_camera_heartbeat <= timeout

    def ack_result(self, command: int) -> int | None:
        return self.acks.get(command)


def _text(raw) -> str:
    if isinstance(raw, bytes):
        raw = raw.split(b"\x00")[0].decode("utf-8", "replace")
    return str(raw).rstrip("\x00")
