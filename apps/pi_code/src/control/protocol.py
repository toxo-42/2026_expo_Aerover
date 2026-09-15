"""제어 규약: 명령 문자열 한 줄 → JSON 한 줄 응답. 전송 수단(소켓)과 무관하다."""
from __future__ import annotations

import json
from typing import Callable, Mapping, Protocol

Handler = Callable[[], dict]


class Controllable(Protocol):
    def start(self) -> dict: ...
    def stop(self) -> dict: ...
    def status(self) -> dict: ...
    def toggle(self) -> dict: ...


def handlers_for(target: Controllable) -> dict[str, Handler]:
    return {"start": target.start,
            "stop": target.stop,
            "status": target.status,
            "toggle": target.toggle}


def dispatch(raw: bytes, handlers: Mapping[str, Handler]) -> bytes:
    cmd = raw.decode().strip().lower()
    fn = handlers.get(cmd)
    res = fn() if fn else {"ok": False, "msg": f"알 수 없는 명령: {cmd}"}
    return (json.dumps(res, ensure_ascii=False) + "\n").encode()
