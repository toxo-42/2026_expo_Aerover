"""시리얼 수신 스레드 · 앱 전체가 공유하는 기본 인스턴스.

수신 스레드는 **`start()` 를 불러야 뜬다.** import 만으로 포트를 열지 않는다 —
테스트나 다른 모듈이 import 하는 것만으로 조종기를 붙잡으면 안 된다.

`SerialReader` 는 포트를 여는 방법을 주입받는다. 진짜 pyserial 이든 바이트를
흘려주는 가짜든 `read(n)` 만 있으면 된다.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, ContextManager, Protocol

from src.config import SERIAL_BAUD, SERIAL_PORT
from src.core.telemetry.crsf import frames
from src.core.telemetry.state import TelemetryStore
from src.core.telemetry.status import LinkJudge

RETRY_SEC = 2.0       # 포트가 없거나 케이블이 빠졌을 때 다시 시도하는 간격
READ_CHUNK = 256


class BytePort(Protocol):
    def read(self, size: int) -> bytes: ...


PortOpener = Callable[[], ContextManager[BytePort]]


def open_serial_port(port: str = SERIAL_PORT, baud: int = SERIAL_BAUD) -> ContextManager[BytePort]:
    """pyserial 포트. 지연 import — pyserial 이 없어도 앱은 뜨고 오류만 표시된다."""
    import serial
    return serial.Serial(port, baud, timeout=0.1)


class SerialReader(threading.Thread):
    """포트에서 CRSF 프레임을 계속 읽어 store 를 갱신한다. 포트가 죽으면 다시 연다."""

    def __init__(self, store: TelemetryStore, open_port: PortOpener,
                 retry_sec: float = RETRY_SEC,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        super().__init__(daemon=True, name="crsf-rx")
        self._store = store
        self._open_port = open_port
        self._retry_sec = retry_sec
        self._sleep = sleep
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        buf = bytearray()
        while not self._stop_event.is_set():
            try:
                with self._open_port() as port:
                    self._store.set_error(None)
                    buf.clear()
                    self._pump(port, buf)
            except Exception as e:      # 포트 없음·케이블 빠짐 — 죽지 말고 재접속
                self._store.set_error(f"{type(e).__name__}: {e}")
                self._sleep(self._retry_sec)

    def _pump(self, port: BytePort, buf: bytearray) -> None:
        while not self._stop_event.is_set():
            chunk = port.read(READ_CHUNK)
            if not chunk:
                continue
            buf += chunk
            for kind, data in frames(buf):
                self._store.update(kind, data)


# ---- 앱 전체가 쓰는 기본 인스턴스 ----

_store = TelemetryStore()
_judge = LinkJudge()
_reader: SerialReader | None = None
_reader_lock = threading.Lock()


def start() -> None:
    """수신 스레드를 띄운다. 여러 번 불러도 하나만 뜬다."""
    global _reader
    with _reader_lock:
        if _reader is None:
            _reader = SerialReader(_store, open_serial_port)
            _reader.start()


def get_telemetry() -> dict:
    """현재 텔레메트리 스냅샷(복사본)."""
    return _store.snapshot()


def store() -> TelemetryStore:
    """앱이 공유하는 상태 보관함. MAVLink 로 받은 FC 텔레메트리도 여기에 넣는다 (`telemetry.mavlink`)."""
    return _store


def link_status(snap: dict) -> str:
    """"NO_DATA" · "LOST" · "WEAK" · "OK" — 기준은 `status.LinkJudge`."""
    return _judge.status(snap)
