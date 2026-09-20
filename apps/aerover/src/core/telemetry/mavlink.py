"""MAVLink 텔레메트리 → 공유 상태. FC 가 MAVLink 로 보내는 값을 CRSF 와 **같은 키**에 넣는다.

파이가 FC 의 MAVLink UART 를 UDP 로 중계하면(`pi_code` 의 `DRONECAM_FC_SERIAL`) 계기판은
조종기 USB(CRSF) 없이도 값을 받는다. 둘 다 오면 나중에 온 값이 남는다.

메시지 하나 = 함수 하나. 새 메시지는 `HANDLERS` 에 한 줄 더한다.
pymavlink 없이도 import 된다 — 메시지 객체의 속성만 읽는다.
"""
from __future__ import annotations

import math
from typing import Callable

from src.core.telemetry.state import TelemetryStore

UNKNOWN_U16 = 0xFFFF        # MAVLink 의 "모름" 표시
UNKNOWN_U8 = 0xFF
UNKNOWN_I = -1

ARMED_FLAG = 0x80           # base_mode 의 MAV_MODE_FLAG_SAFETY_ARMED
AUTOPILOT_COMPONENT = 1     # MAV_COMP_ID_AUTOPILOT1 — FC 가 쓰는 컴포넌트 번호
RSSI_UNKNOWN = 255          # RC_CHANNELS.rssi 의 "모름"
RSSI_MAX = 254              # rssi 실제 범위는 0~254

Update = tuple[str, dict]                   # (상태 키, 갱신할 값)
Handler = Callable[[object], list[Update]]


def _gps_raw_int(m) -> list[Update]:
    return [("gps", {
        "lat": m.lat / 1e7,
        "lon": m.lon / 1e7,
        "alt_m": int(round(m.alt / 1000)),                                   # mm → m
        "speed_kmh": 0.0 if m.vel == UNKNOWN_U16 else m.vel * 0.036,         # cm/s → km/h
        "heading": 0.0 if m.cog == UNKNOWN_U16 else m.cog / 100,             # cdeg → deg
        "sats": 0 if m.satellites_visible == UNKNOWN_U8 else m.satellites_visible,
    })]


def _attitude(m) -> list[Update]:
    return [("attitude", {"roll": math.degrees(m.roll), "pitch": math.degrees(m.pitch),
                          "yaw": math.degrees(m.yaw) % 360})]


def _sys_status(m) -> list[Update]:
    d: dict = {}
    if m.voltage_battery != UNKNOWN_U16:
        d["voltage"] = m.voltage_battery / 1000                              # mV → V
    if m.current_battery != UNKNOWN_I:
        d["current"] = m.current_battery / 100                               # cA → A
    if m.battery_remaining != UNKNOWN_I:
        d["remaining_pct"] = m.battery_remaining
    return [("battery", d)] if d else []


def _battery_status(m) -> list[Update]:
    d: dict = {}
    if m.current_consumed != UNKNOWN_I:
        d["used_mah"] = m.current_consumed
    if m.battery_remaining != UNKNOWN_I:
        d["remaining_pct"] = m.battery_remaining
    return [("battery", d)] if d else []


def _heartbeat(m) -> list[Update]:
    """arming 상태 → 모드 문자열. CRSF 의 FLIGHT_MODE(0x21) 자리를 채운다.

    **FC 가 보낸 것만 받는다.** 파이 카메라 노드도 HEARTBEAT 를 보내므로
    (`core/gcs.py` 의 `CAMERA_COMPONENT_ID`) 거르지 않으면 카메라 상태가 FC 를 덮는다.

    `custom_mode`(INAV 비행모드 번호)는 쓰지 않는다 — 2026-09-20 실측에서 disarmed 중
    22 로 고정이었고 값의 뜻을 확인하지 못했다. 확인되면 여기서 문자열로 바꾼다.
    """
    if m.get_srcComponent() != AUTOPILOT_COMPONENT:
        return []
    return [("mode", {"text": "ARMED" if m.base_mode & ARMED_FLAG else "DISARMED"})]


def _rc_channels(m) -> list[Update]:
    """조종 링크 품질 → CRSF LINK_STATS(0x14) 자리. **조종기 USB 없이 LINK 칩을 채운다.**

    `rssi` 는 0~254 라 계기판이 쓰는 0~100 으로 줄인다 (`telemetry/status.py` 의
    `WEAK_BELOW` 가 % 기준이다). dBm(`up_rssi1`)은 MAVLink 에 없어서 건드리지 않는다 —
    없는 값을 지어내면 화면이 조용히 거짓말을 한다.

    CRSF 와 둘 다 오면 나중에 온 값이 남는다. INAV 의 RSSI 소스가 CRSF LQ 면 두 값이
    거의 같다 (2026-09-20 실측: MAVLink rssi 254 ↔ CRSF LQ 100).
    """
    if m.rssi == RSSI_UNKNOWN:
        return []
    return [("link", {"source": "mavlink", "up_lq": round(m.rssi * 100 / RSSI_MAX)})]


def _vfr_hud(m) -> list[Update]:
    return [("baro", {"alt_m": float(m.alt)}), ("vario", {"vspeed_ms": float(m.climb)})]


HANDLERS: dict[str, Handler] = {
    "HEARTBEAT": _heartbeat,
    "RC_CHANNELS": _rc_channels,
    "GPS_RAW_INT": _gps_raw_int,
    "ATTITUDE": _attitude,
    "SYS_STATUS": _sys_status,
    "BATTERY_STATUS": _battery_status,
    "VFR_HUD": _vfr_hud,
}


def apply_mavlink(store: TelemetryStore, msg) -> tuple[str, ...]:
    """메시지를 store 에 반영하고 갱신한 키를 돌려준다. 모르는 메시지면 빈 튜플."""
    handler = HANDLERS.get(msg.get_type())
    if handler is None:
        return ()
    updates = handler(msg)
    for kind, data in updates:
        store.update(kind, data)
    return tuple(kind for kind, _ in updates)
