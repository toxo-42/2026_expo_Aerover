from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol

from src.hardware.led import StatusLed
from src.log import log


class Toggleable(Protocol):
    @property
    def recording(self) -> bool: ...

    def toggle(self) -> dict: ...


@dataclass(frozen=True)
class ButtonSettings:
    hold_sec: float = 3.0            # 이 시간 이상 누르면 종료로 판정
    enable_hold_exit: bool = False   # 길게 눌러 데몬 종료 기능 (오조작이 걱정되면 False)
    min_press_sec: float = 0.0       # 이 시간보다 짧게 눌리면 무시 (예: 0.3 → 스치는 접촉 방지)


class ButtonControl:
    """버튼 하나로 토글 + 길게 눌러 종료.

    when_released 시점에 판정하므로, 길게 누른 경우 토글이 함께 일어나지 않는다.
    콜백은 gpiozero 내부 스레드에서 실행되고 Recorder는 락으로 보호되므로
    소켓 명령과 동시에 들어와도 안전하다.
    """

    def __init__(self, button, target: Toggleable, led: StatusLed,
                 on_exit: Callable[[], None], settings: ButtonSettings) -> None:
        self.btn = button
        self.target = target
        self.led = led
        self.on_exit = on_exit
        self.settings = settings
        self._held = False
        self._t_down = 0.0

        self.btn.when_pressed = self._on_pressed
        self.btn.when_held = self._on_held
        self.btn.when_released = self._on_released

    def _on_pressed(self) -> None:
        self._t_down = time.time()

    def _on_held(self) -> None:
        if not self.settings.enable_hold_exit:
            return
        self._held = True
        log(f"버튼 {self.settings.hold_sec}s 이상 눌림 → 데몬 종료 요청")
        self.led.flash(2.0)
        self.on_exit()

    def _on_released(self) -> None:
        if self._held:                 # 종료용 길게 누름이었음
            self._held = False
            return
        if time.time() - self._t_down < self.settings.min_press_sec:
            return                     # 스치듯 눌린 입력 무시

        res = self.target.toggle()
        log(f"버튼 입력 → {res.get('msg')}")
        if not res.get("ok"):
            self.led.flash(1.5)
        self.led.update(self.target.recording)

    def close(self) -> None:
        self.btn.close()
