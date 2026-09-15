"""드론 텔레메트리 — 조종기 USB 의 CRSF 와 파이가 중계하는 MAVLink 를 받아 계기판에 넘긴다.

    crsf.py      CRSF 프레임 파싱 (순수 함수 — 기체 없이 테스트한다)
    mavlink.py   MAVLink 메시지 → 같은 상태 키 (파이가 FC 의 MAVLink UART 를 UDP 로 중계할 때)
    state.py     공유 상태 보관함 · 나이(age) · 오류
    status.py    연결 판정 (NO_DATA / LOST / WEAK / OK)
    home.py      이륙지점 · 거리 · 방위
    receiver.py  시리얼 수신 스레드 · 앱이 공유하는 기본 인스턴스

화면은 이 패키지가 내놓는 이름만 쓴다. `start()` 를 부르기 전에는 수신하지 않는다.
"""
from src.core.telemetry.home import (HomePoint, bearing_from_home, clear_home,
                                     distance_from_home, home_is_set, set_home)
from src.core.telemetry.mavlink import apply_mavlink
from src.core.telemetry.receiver import SerialReader, get_telemetry, link_status, start, store
from src.core.telemetry.state import TelemetryStore, age, last_error
from src.core.telemetry.status import LinkJudge

__all__ = [
    "start", "get_telemetry", "age", "link_status", "last_error", "store", "apply_mavlink",
    "set_home", "clear_home", "home_is_set", "distance_from_home", "bearing_from_home",
    "TelemetryStore", "LinkJudge", "HomePoint", "SerialReader",
]
