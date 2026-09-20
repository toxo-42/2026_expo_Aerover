"""연결 판정 — 스냅샷을 보고 NO_DATA / LOST / WEAK / OK 중 하나를 정한다.

**프레임 유무로 판정하지 않는다.** LINK_STATS(0x14) 는 FC 가 아니라 조종기 안의
TX 모듈이 만들어서, 기체 전원이 꺼져도 계속 온다. 그래서
  · 프레임 자체가 안 오면          → USB/핸드셋 문제        (NO_DATA)
  · 프레임은 오는데 up_lq 가 0 이면 → 전파 링크 끊김         (LOST, 잠깐이면 WEAK)
로 나눈다.

"프레임이 안 온다"의 기준은 **값을 준 경로마다 다르다.** 조종기 USB(CRSF)는 0.2초 주기라
0.3초면 끊긴 것이지만, 파이가 중계하는 MAVLink RC_CHANNELS 는 1~4초 간격으로 온다.
그래서 `link["source"]` 를 보고 임계값을 고른다.
"""
from __future__ import annotations

import time
from typing import Callable

from src.core.telemetry.state import age

NO_DATA_SEC = 0.3     # LINK_STATS 가 이보다 오래 안 오면 NO_DATA (실측 주기 약 0.2초)
# 파이가 중계하는 RC_CHANNELS 는 훨씬 느리다 — 2026-09-20 실측 0.6Hz, 간격 1.0~4.2초.
# 여기에 0.3 을 쓰면 값이 멀쩡히 들어와도 계속 NO_DATA 로 읽힌다.
MAVLINK_NO_DATA_SEC = 5.0
LOST_SEC = 1.0        # up_lq == 0 이 이보다 오래가면 LOST
WEAK_BELOW = 60       # up_lq 가 이 미만이면 WEAK


class LinkJudge:
    """`up_lq == 0` 이 얼마나 지속됐는지 기억해야 해서 상태를 가진다."""

    def __init__(self, no_data_sec: float = NO_DATA_SEC, lost_sec: float = LOST_SEC,
                 weak_below: int = WEAK_BELOW,
                 mavlink_no_data_sec: float = MAVLINK_NO_DATA_SEC,
                 clock: Callable[[], float] = time.time) -> None:
        self.no_data_sec = no_data_sec
        self.mavlink_no_data_sec = mavlink_no_data_sec
        self.lost_sec = lost_sec
        self.weak_below = weak_below
        self._clock = clock
        self._lq_zero_since: float | None = None

    def _no_data_limit(self, snap: dict) -> float:
        """값을 준 경로의 수신 주기에 맞춘 임계값. 출처를 모르면 엄한 쪽(CRSF)을 쓴다."""
        if snap["link"].get("source") == "mavlink":
            return self.mavlink_no_data_sec
        return self.no_data_sec

    def status(self, snap: dict) -> str:
        now = self._clock()
        a = age(snap, "link", now)
        if a is None or a > self._no_data_limit(snap):
            return "NO_DATA"

        lq = snap["link"]["up_lq"]
        if lq == 0:
            if self._lq_zero_since is None:
                self._lq_zero_since = now
            return "LOST" if now - self._lq_zero_since >= self.lost_sec else "WEAK"

        self._lq_zero_since = None
        return "WEAK" if lq < self.weak_below else "OK"
