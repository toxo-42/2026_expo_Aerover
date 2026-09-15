"""연결 판정 — 스냅샷을 보고 NO_DATA / LOST / WEAK / OK 중 하나를 정한다.

**프레임 유무로 판정하지 않는다.** LINK_STATS(0x14) 는 FC 가 아니라 조종기 안의
TX 모듈이 만들어서, 기체 전원이 꺼져도 계속 온다. 그래서
  · 프레임 자체가 안 오면          → USB/핸드셋 문제        (NO_DATA)
  · 프레임은 오는데 up_lq 가 0 이면 → 전파 링크 끊김         (LOST, 잠깐이면 WEAK)
로 나눈다.
"""
from __future__ import annotations

import time
from typing import Callable

from src.core.telemetry.state import age

NO_DATA_SEC = 0.3     # LINK_STATS 가 이보다 오래 안 오면 NO_DATA (실측 주기 약 0.2초)
LOST_SEC = 1.0        # up_lq == 0 이 이보다 오래가면 LOST
WEAK_BELOW = 60       # up_lq 가 이 미만이면 WEAK


class LinkJudge:
    """`up_lq == 0` 이 얼마나 지속됐는지 기억해야 해서 상태를 가진다."""

    def __init__(self, no_data_sec: float = NO_DATA_SEC, lost_sec: float = LOST_SEC,
                 weak_below: int = WEAK_BELOW,
                 clock: Callable[[], float] = time.time) -> None:
        self.no_data_sec = no_data_sec
        self.lost_sec = lost_sec
        self.weak_below = weak_below
        self._clock = clock
        self._lq_zero_since: float | None = None

    def status(self, snap: dict) -> str:
        now = self._clock()
        a = age(snap, "link", now)
        if a is None or a > self.no_data_sec:
            return "NO_DATA"

        lq = snap["link"]["up_lq"]
        if lq == 0:
            if self._lq_zero_since is None:
                self._lq_zero_since = now
            return "LOST" if now - self._lq_zero_since >= self.lost_sec else "WEAK"

        self._lq_zero_since = None
        return "WEAK" if lq < self.weak_below else "OK"
