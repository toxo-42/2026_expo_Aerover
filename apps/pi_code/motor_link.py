#!/usr/bin/env python3
"""G031 TB6612 모터 보드 링크 (Raspberry Pi 측).

프레임:  STX(0xAA) | CMD | LEN | DATA[LEN] | CRC16_LSB | CRC16_MSB
CRC:     CRC-16/XMODEM (poly 0x1021, init 0x0000), CMD..DATA 구간

펌웨어는 FAILSAFE_TIMEOUT_MS(200ms) 동안 유효 패킷이 없으면 STBY 를 내려
모터를 끊는다. 그래서 MotorLink 는 기본적으로 keepalive 스레드를 띄워
Ping 을 계속 흘린다 — 끄면 명령 사이에 모터가 툭툭 선다.

    from motor_link import MotorLink
    with MotorLink("/dev/ttyAMA5") as m:
        m.set_both(500, -500)
        print(m.status())
"""

from __future__ import annotations

import argparse
import struct
import sys
import threading
import time
from dataclasses import dataclass

import serial  # pyserial

STX = 0xAA
ACK_FLAG = 0x80
MAX_PACKET_LEN = 24

CMD_PING = 0x00
CMD_SET_MOTOR = 0x01
CMD_SET_BOTH = 0x02
CMD_COAST = 0x03
CMD_BRAKE = 0x04
CMD_SET_STANDBY = 0x05
CMD_GET_STATUS = 0x06
CMD_CLEAR_ERR = 0x07
CMD_GET_BATTERY = 0x08
CMD_ERR = 0xFF

# 0 은 "오류 없음" 이라 목록에 없다. Status.last_err 가 0 이면 깨끗한 상태다.
ERR_NAMES = {
    0x01: "Comm_Err",
    0x02: "Bad_Length",
    0x03: "Bad_Argument",
    0x04: "Not_Init",
    0x05: "Unknown_Cmd",
}

DUTY_MAX = 1000

# 팩 직렬 수. 펌웨어는 팩 전압만 보내고 셀당 환산은 여기서 한다.
BATTERY_CELLS = 6

# 펌웨어 타임아웃의 1/4. 한두 프레임을 놓쳐도 트립되지 않을 만큼 여유가 있고,
# 115200bps 에서 5B 패킷이라 대역폭은 거의 안 먹는다.
KEEPALIVE_PERIOD_S = 0.05


def crc16(data: bytes) -> int:
    crc = 0x0000
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build(cmd: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PACKET_LEN:
        raise ValueError(f"payload too long: {len(payload)} > {MAX_PACKET_LEN}")
    body = bytes([cmd, len(payload)]) + payload
    return bytes([STX]) + body + struct.pack("<H", crc16(body))


class CommError(Exception):
    pass


class DeviceError(CommError):
    def __init__(self, code: int):
        super().__init__(f"device error 0x{code:02X} ({ERR_NAMES.get(code, 'unknown')})")
        self.code = code


@dataclass
class Status:
    duty_a: int
    duty_b: int
    standby: bool
    tripped: bool       # 이 status 요청이 도착한 순간 트립 상태였다
    driver_ready: bool
    trip_latched: bool  # 마지막 clear_err() 이후 트립이 한 번이라도 났다
    last_err: int
    rx_ok: int
    crc_err: int

    @classmethod
    def unpack(cls, payload: bytes) -> "Status":
        if len(payload) != 10:
            raise CommError(f"status payload must be 10B, got {len(payload)}")
        duty_a, duty_b, flags, last_err, rx_ok, crc_err = struct.unpack("<hhBBHH", payload)
        return cls(
            duty_a=duty_a,
            duty_b=duty_b,
            standby=bool(flags & 0x01),
            tripped=bool(flags & 0x02),
            driver_ready=bool(flags & 0x04),
            trip_latched=bool(flags & 0x08),
            last_err=last_err,
            rx_ok=rx_ok,
            crc_err=crc_err,
        )

    def __str__(self) -> str:
        err = "-" if self.last_err == 0 else ERR_NAMES.get(self.last_err, hex(self.last_err))
        return (
            f"A={self.duty_a:+5d}  B={self.duty_b:+5d}  (permille)  "
            f"standby={'Y' if self.standby else 'n'} "
            f"tripped={'Y' if self.tripped else 'n'} "
            f"latched={'Y' if self.trip_latched else 'n'} "
            f"ready={'Y' if self.driver_ready else 'n'}  "
            f"rx_ok={self.rx_ok} crc_err={self.crc_err} last_err={err}"
        )


@dataclass
class Battery:
    mv: int        # IIR(시정수 ≈320ms) 을 거친 팩 전압. 첫 샘플 전이면 0
    mv_raw: int    # 마지막 변환 한 번의 환산값
    adc_raw: int   # 마지막 ADC 코드(12bit)

    @classmethod
    def unpack(cls, payload: bytes) -> "Battery":
        if len(payload) != 6:
            raise CommError(f"battery payload must be 6B, got {len(payload)}")
        return cls(*struct.unpack("<HHH", payload))

    @property
    def volts(self) -> float:
        return self.mv / 1000.0

    @property
    def cell_volts(self) -> float:
        return self.volts / BATTERY_CELLS

    def __str__(self) -> str:
        return (
            f"{self.volts:6.2f} V  ({self.cell_volts:.3f} V/cell x{BATTERY_CELLS})  "
            f"raw={self.mv_raw / 1000.0:.2f} V  adc={self.adc_raw}"
        )


class MotorLink:
    def __init__(
        self,
        port: str = "/dev/ttyAMA5",
        baud: int = 115200,
        timeout: float = 0.25,
        keepalive: bool = True,
    ):
        self._ser = serial.Serial(port, baud, timeout=0.01)
        self._timeout = timeout
        self._lock = threading.RLock()
        self._rx = bytearray()
        self._stop = threading.Event()
        self._ka: threading.Thread | None = None

        # 포트를 열면 Pi 쪽 FIFO 에 부팅 중 흘러든 쓰레기가 남아 있다.
        time.sleep(0.05)
        self._ser.reset_input_buffer()

        if keepalive:
            self._ka = threading.Thread(target=self._keepalive_loop, daemon=True)
            self._ka.start()

    # ── 트랜잭션 ─────────────────────────────────────────────────────────────
    def _xfer(self, cmd: int, payload: bytes = b"") -> bytes:
        """명령을 보내고 대응하는 ACK(cmd|0x80) 를 기다린다."""
        with self._lock:
            self._ser.write(build(cmd, payload))
            self._ser.flush()
            want = cmd | ACK_FLAG
            deadline = time.monotonic() + self._timeout

            while True:
                rcmd, data = self._read_frame(deadline)
                if rcmd == CMD_ERR:
                    raise DeviceError(data[0] if data else 0xFF)
                if rcmd == want:
                    return data
                # 다른 ACK 는 앞선 트랜잭션의 늦은 응답이다. 버리고 계속 기다린다.

    def _read_frame(self, deadline: float) -> tuple[int, bytes]:
        while True:
            while True:
                total = 0
                if len(self._rx) >= 3:
                    if self._rx[0] != STX or self._rx[2] > MAX_PACKET_LEN:
                        del self._rx[0]
                        continue
                    total = 3 + self._rx[2] + 2
                    if len(self._rx) >= total:
                        break
                if time.monotonic() >= deadline:
                    raise CommError("timeout waiting for response")
                # read(n) 은 n 바이트가 다 차거나 타임아웃이 나야 돌아온다. 64 처럼
                # 크게 부르면 응답이 이미 와 있어도 매번 타임아웃을 꽉 채운다.
                # 쌓인 만큼만, 없으면 1 바이트를 기다려 첫 바이트에 바로 깨어난다.
                chunk = self._ser.read(max(1, self._ser.in_waiting))
                if chunk:
                    self._rx.extend(chunk)

            frame = bytes(self._rx[:total])
            body = frame[1:-2]
            got = struct.unpack("<H", frame[-2:])[0]
            del self._rx[:total]

            if got == crc16(body):
                return body[0], body[2:]
            # CRC 가 틀리면 프레임 경계를 잘못 잡은 것이다. 1B 밀고 다시 찾는다.
            self._rx[:0] = frame[1:]

    def _keepalive_loop(self) -> None:
        while not self._stop.wait(KEEPALIVE_PERIOD_S):
            try:
                self._xfer(CMD_PING)
            except CommError:
                pass  # 링크가 잠깐 끊겨도 스레드는 살려 둔다. 트립은 MCU 가 알아서 한다.

    # ── 명령 ────────────────────────────────────────────────────────────────
    def ping(self) -> None:
        self._xfer(CMD_PING)

    def set_motor(self, ch: int, duty: int) -> None:
        """ch: 0=A, 1=B. duty: -1000..+1000 퍼밀, 부호가 방향."""
        if ch not in (0, 1):
            raise ValueError("ch must be 0 (A) or 1 (B)")
        self._xfer(CMD_SET_MOTOR, struct.pack("<Bh", ch, _clamp(duty)))

    def set_both(self, duty_a: int, duty_b: int) -> None:
        self._xfer(CMD_SET_BOTH, struct.pack("<hh", _clamp(duty_a), _clamp(duty_b)))

    def coast(self) -> None:
        self._xfer(CMD_COAST)

    def brake(self) -> None:
        self._xfer(CMD_BRAKE)

    def standby(self, on: bool) -> None:
        self._xfer(CMD_SET_STANDBY, bytes([1 if on else 0]))

    def status(self) -> Status:
        return Status.unpack(self._xfer(CMD_GET_STATUS))

    def clear_err(self) -> None:
        self._xfer(CMD_CLEAR_ERR)

    def battery(self) -> Battery:
        return Battery.unpack(self._xfer(CMD_GET_BATTERY))

    # ── 수명 ────────────────────────────────────────────────────────────────
    def close(self) -> None:
        self._stop.set()
        if self._ka:
            self._ka.join(timeout=1.0)
        try:
            self.coast()
        except CommError:
            pass
        self._ser.close()

    def __enter__(self) -> "MotorLink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _clamp(duty: int) -> int:
    return max(-DUTY_MAX, min(DUTY_MAX, int(duty)))


# ── CLI ─────────────────────────────────────────────────────────────────────
def _repl(link: MotorLink) -> None:
    print("a <duty> | b <duty> | both <a> <b> | stop | brake | standby on|off | s | bat | q")
    while True:
        try:
            line = input("> ").strip().split()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        op, args = line[0], line[1:]
        try:
            if op in ("q", "quit", "exit"):
                return
            elif op == "a":
                link.set_motor(0, int(args[0]))
            elif op == "b":
                link.set_motor(1, int(args[0]))
            elif op == "both":
                link.set_both(int(args[0]), int(args[1]))
            elif op in ("stop", "coast"):
                link.coast()
            elif op == "brake":
                link.brake()
            elif op == "standby":
                link.standby(args[0] in ("on", "1", "true"))
            elif op in ("s", "status"):
                print(link.status())
            elif op in ("bat", "battery"):
                print(link.battery())
            else:
                print(f"? {op}")
        except (CommError, ValueError, IndexError) as e:
            print(f"error: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="G031 TB6612 motor link")
    p.add_argument("--port", default="/dev/ttyAMA5")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--no-keepalive", action="store_true",
                   help="Ping 스레드를 끈다. 200ms 안에 명령이 없으면 모터가 선다.")

    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    sub.add_parser("status")
    sub.add_parser("battery", aliases=["bat"])
    sub.add_parser("stop")
    sub.add_parser("brake")
    sub.add_parser("clear")
    sub.add_parser("repl")

    sp = sub.add_parser("set", help="한 채널 duty")
    sp.add_argument("ch", type=int, choices=(0, 1))
    sp.add_argument("duty", type=int)

    sp = sub.add_parser("both", help="두 채널 duty")
    sp.add_argument("duty_a", type=int)
    sp.add_argument("duty_b", type=int)

    sp = sub.add_parser("standby")
    sp.add_argument("state", choices=("on", "off"))

    sp = sub.add_parser("run", help="duty 를 주고 지정 시간 유지한 뒤 정지")
    sp.add_argument("duty_a", type=int)
    sp.add_argument("duty_b", type=int)
    sp.add_argument("seconds", type=float)

    a = p.parse_args(argv)

    # 단발 명령은 프로세스가 곧 끝나 모터가 어차피 선다. 그 동작이 맞다 —
    # 이어서 돌리고 싶으면 repl 이나 run 을 쓴다.
    need_ka = a.cmd in ("repl", "run") and not a.no_keepalive

    try:
        link = MotorLink(a.port, a.baud, keepalive=need_ka)
    except serial.SerialException as e:
        print(f"cannot open {a.port}: {e}", file=sys.stderr)
        return 1

    try:
        if a.cmd == "ping":
            link.ping()
            print("pong")
        elif a.cmd == "status":
            print(link.status())
        elif a.cmd in ("battery", "bat"):
            print(link.battery())
        elif a.cmd == "stop":
            link.coast()
        elif a.cmd == "brake":
            link.brake()
        elif a.cmd == "clear":
            link.clear_err()
        elif a.cmd == "set":
            link.set_motor(a.ch, a.duty)
        elif a.cmd == "both":
            link.set_both(a.duty_a, a.duty_b)
        elif a.cmd == "standby":
            link.standby(a.state == "on")
        elif a.cmd == "run":
            link.set_both(a.duty_a, a.duty_b)
            time.sleep(a.seconds)
            link.coast()
        elif a.cmd == "repl":
            _repl(link)
    except CommError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        link.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
