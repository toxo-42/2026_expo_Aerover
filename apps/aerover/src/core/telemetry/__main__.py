"""단독 실행 — GUI 없이 수신만 확인한다.

    uv run python -m src.core.telemetry
"""
import time

from src.config import SERIAL_PORT
from src.core.telemetry import get_telemetry, last_error, link_status, start


def line(snap: dict) -> str:
    g, b, k = snap["gps"], snap["battery"], snap["link"]
    return (f"alt {g['alt_m']:4d}m  sats {g['sats']:2d}  "
            f"hdg {g['heading']:6.1f}  spd {g['speed_kmh']:4.1f}  |  "
            f"{b['voltage']:5.1f}V {b['current']:5.1f}A {b['remaining_pct']:3d}%  |  "
            f"LQ {k['up_lq']:3d}  RSSI {k['up_rssi1']:4d}  {link_status(snap)}")


def main() -> None:
    print(f"포트 {SERIAL_PORT} — Ctrl+C 로 끝낸다")
    start()
    while True:
        snap = get_telemetry()
        err = last_error(snap)
        print(f"오류 — {err}" if err else line(snap))
        time.sleep(0.5)


if __name__ == "__main__":
    main()
