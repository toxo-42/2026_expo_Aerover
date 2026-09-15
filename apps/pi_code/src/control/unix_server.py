from __future__ import annotations

import os
import socket
import threading
from pathlib import Path
from typing import Callable, Mapping

from src.control.protocol import Handler, dispatch
from src.log import log

SOCKET_MODE = 0o660         # 같은 그룹만 명령을 보낼 수 있다
MAX_COMMAND = 256


class UnixCommandServer:
    """유닉스 소켓으로 명령을 받아 처리 함수에 넘긴다.

    accept 타임아웃(poll_sec)마다 on_tick 을 불러, 명령이 없어도 LED 같은 주기 작업이 돈다.
    """

    def __init__(self, path: str, handlers: Mapping[str, Handler],
                 on_tick: Callable[[], None], poll_sec: float = 0.5) -> None:
        self.path = Path(path)
        self.handlers = handlers
        self.on_tick = on_tick
        self.poll_sec = poll_sec
        self._srv: socket.socket | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._unlink()                          # 지난 실행이 남긴 소켓

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.path))
        os.chmod(self.path, SOCKET_MODE)
        srv.listen(4)
        srv.settimeout(self.poll_sec)
        self._srv = srv
        log(f"소켓 대기 중 {self.path}")

    def serve(self, running: threading.Event) -> None:
        assert self._srv is not None, "open() 을 먼저 불러야 한다"
        while running.is_set():
            self.on_tick()
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.sendall(dispatch(conn.recv(MAX_COMMAND), self.handlers))
            self.on_tick()

    def close(self) -> None:
        if self._srv is not None:
            self._srv.close()
            self._srv = None
        self._unlink()

    def _unlink(self) -> None:
        if self.path.exists():
            self.path.unlink()
