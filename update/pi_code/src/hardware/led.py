from __future__ import annotations

import time


class StatusLed:
    """상태 LED. gpiozero의 백그라운드 blink를 쓰므로 별도 스레드가 필요 없다.

    led 가 None 이면(GPIO 없음) 모든 호출이 아무것도 하지 않는다.
    """

    def __init__(self, led) -> None:
        self._led = led
        self._mode: str | None = None
        self._flash_until = 0.0

    def _apply(self, mode: str) -> None:
        if self._led is None or mode == self._mode:
            return
        self._mode = mode
        if mode == "idle":
            self._led.blink(on_time=0.05, off_time=1.95)   # 대기: 살아있음 표시
        elif mode == "rec":
            self._led.on()                                 # 녹화 중: 상시 점등
        elif mode == "warn":
            self._led.blink(on_time=0.08, off_time=0.08)   # 경고: 빠른 점멸
        else:
            self._led.off()

    def flash(self, sec: float = 1.5) -> None:
        """일정 시간 경고 점멸 (명령 실패, 프레임 결손 발생 등)."""
        self._flash_until = time.time() + sec

    def update(self, recording: bool) -> None:
        if time.time() < self._flash_until:
            self._apply("warn")
        else:
            self._apply("rec" if recording else "idle")

    def close(self) -> None:
        if self._led is not None:
            self._led.off()
            self._led.close()
