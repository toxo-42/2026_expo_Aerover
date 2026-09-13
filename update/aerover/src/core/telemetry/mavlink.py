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


def _vfr_hud(m) -> list[Update]:
    return [("baro", {"alt_m": float(m.alt)}), ("vario", {"vspeed_ms": float(m.climb)})]


HANDLERS: dict[str, Handler] = {
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
