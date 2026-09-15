"""gpiozero 가 없거나 권한이 없어도 나머지가 돌도록, 하드웨어 객체 생성을 여기로 모은다."""
from __future__ import annotations

try:
    from gpiozero import LED, Button
    GPIO_ERROR: Exception | None = None
except Exception as _e:          # gpiozero 미설치 / 권한 없음
    LED = Button = None
    GPIO_ERROR = _e


def gpio_available() -> bool:
    return GPIO_ERROR is None


def make_led(pin: int | None):
    return LED(pin) if (gpio_available() and pin is not None) else None


def make_button(pin: int, bounce_sec: float, hold_sec: float):
    # 스위치 ── GND 배선이라 내부 풀업을 쓴다
    return Button(pin, pull_up=True, bounce_time=bounce_sec, hold_time=hold_sec)
