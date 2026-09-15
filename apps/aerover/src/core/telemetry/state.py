"""수신 스레드와 화면이 공유하는 텔레메트리 상태.

키 이름과 개수는 조종기(CRSF)가 보내는 프레임과 같다 (`crsf.FRAMES`).
화면은 `snapshot()` 이 준 복사본만 읽는다 — 그리는 도중 값이 바뀌지 않는다.
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Callable

KINDS = ("gps", "battery", "link", "attitude", "baro", "vario", "mode")


def initial_state() -> dict:
    return {
        "gps": {"lat": 0.0, "lon": 0.0, "speed_kmh": 0.0, "heading": 0.0,
                "alt_m": 0, "sats": 0},
        "battery": {"voltage": 0.0, "current": 0.0, "used_mah": 0, "remaining_pct": 0},
        "link": {"up_rssi1": 0, "up_rssi2": 0, "up_lq": 0, "up_snr": 0, "antenna": 0,
                 "rf_mode": 0, "tx_power_idx": 0, "down_rssi": 0, "down_lq": 0,
                 "down_snr": 0},
        # 아래 4개는 GPS 모듈이 없어도 FC 가 보내준다 (2026-09-07 실측).
        "attitude": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},   # deg
        "baro": {"alt_m": 0.0},                                 # 기압 고도 (해발 아님)
        "vario": {"vspeed_ms": 0.0},                            # 상승률 m/s
        "mode": {"text": ""},                                   # 비행 모드 문자열
        "_rx_at": dict.fromkeys(KINDS),                         # 종류별 마지막 수신 시각
        "_error": None,                                         # 수신 스레드의 마지막 오류
    }


class TelemetryStore:
    """스레드 안전한 상태 보관함. 쓰는 쪽은 수신 스레드 하나, 읽는 쪽은 화면이다."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._state = initial_state()

    def update(self, kind: str, data: dict) -> None:
        with self._lock:
            self._state[kind].update(data)
            self._state["_rx_at"][kind] = self._clock()

    def set_error(self, message: str | None) -> None:
        with self._lock:
            self._state["_error"] = message

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)


def age(snap: dict, kind: str, now: float | None = None) -> float | None:
    """kind 프레임을 마지막으로 받은 지 몇 초 됐는지. 한 번도 못 받았으면 None."""
    ts = snap["_rx_at"].get(kind)
    if ts is None:
        return None
    return (time.time() if now is None else now) - ts


def last_error(snap: dict) -> str | None:
    """수신 스레드가 만난 마지막 오류 메시지. 정상이면 None."""
    return snap.get("_error")
