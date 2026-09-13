"""이륙지점(HOME) · 거리 · 방위.

HOME 은 **자동으로 안 잡는다** — 첫 fix 로 잡으면 실내에서 켜고 밖에 나가는
시연 동선에서 엉뚱한 자리가 HOME 이 된다 (2026-09-08 판단). 사용자가 화면의
[HOME 설정] 으로 직접 잡고, 자리를 옮기면 다시 누른다.

거리는 평면 근사다. 위도 1도 ≈ 111.32km, 경도 1도는 그 자리 위도의 cos 만큼 줄어든다.
시연 반경(수백 m)에서는 오차가 cm 단위라 충분하다.
"""
from __future__ import annotations

import math
import threading

M_PER_DEG_LAT = 111_320.0


def _m_per_deg_lon(lat_deg: float) -> float:
    return M_PER_DEG_LAT * math.cos(math.radians(lat_deg))


def _has_fix(snap: dict) -> bool:
    return bool(snap["gps"]["sats"])


class HomePoint:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._lat: float | None = None
        self._lon: float | None = None

    def set(self, snap: dict) -> bool:
        """현재 좌표를 이륙지점으로 잡는다. fix 가 없으면 잡지 않고 False.
        이미 잡혀 있어도 덮어쓴다 — 시연 중 자리를 옮기면 다시 누르는 게 정상 동선이다."""
        if not _has_fix(snap):
            return False
        g = snap["gps"]
        with self._lock:
            self._lat, self._lon = g["lat"], g["lon"]
        return True

    def clear(self) -> None:
        with self._lock:
            self._lat = self._lon = None

    @property
    def is_set(self) -> bool:
        with self._lock:
            return self._lat is not None

    def _offset_m(self, snap: dict) -> tuple[float, float] | None:
        """HOME 기준 (북쪽 m, 동쪽 m). fix 없거나 HOME 미설정이면 None."""
        with self._lock:
            hlat, hlon = self._lat, self._lon
        if hlat is None or not _has_fix(snap):
            return None
        g = snap["gps"]
        north = (g["lat"] - hlat) * M_PER_DEG_LAT
        east = (g["lon"] - hlon) * _m_per_deg_lon(hlat)
        return north, east

    def distance(self, snap: dict) -> float | None:
        """이륙지점으로부터의 수평 거리 (m)."""
        off = self._offset_m(snap)
        return None if off is None else math.hypot(*off)

    def bearing(self, snap: dict) -> float | None:
        """이륙지점 기준 방위각 (deg, 북=0, 시계방향)."""
        off = self._offset_m(snap)
        if off is None:
            return None
        north, east = off
        return math.degrees(math.atan2(east, north)) % 360


# 앱 전체가 공유하는 HOME 하나. 화면은 아래 함수만 쓴다.
_home = HomePoint()


def set_home(snap: dict) -> bool:
    return _home.set(snap)


def clear_home() -> None:
    _home.clear()


def home_is_set() -> bool:
    return _home.is_set


def distance_from_home(snap: dict) -> float | None:
    return _home.distance(snap)


def bearing_from_home(snap: dict) -> float | None:
    return _home.bearing(snap)
