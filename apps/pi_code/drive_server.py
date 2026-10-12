#!/usr/bin/env python3
"""UDP 로 받은 duty 를 MotorLink 로 흘리는 Pi 측 서버.

패킷(8B):  MAGIC(b"DRV1") | seq(u32) | duty_a(i16) | duty_b(i16)   little-endian

클라이언트(drive_gui.py)는 키를 안 누르고 있어도 20Hz 로 현재 목표값을 계속 보낸다.
그래서 서버는 "마지막 패킷 이후 DEADMAN_S 가 지나면 정지" 하나만 지키면 된다 —
GUI 가 죽거나 Wi-Fi 가 끊겨도 모터가 마지막 명령대로 계속 달리지 않는다.

    droneenv/bin/python drive_server.py            # 0.0.0.0:9000, /dev/ttyAMA5
    droneenv/bin/python drive_server.py -v         # + 1초 요약, 거부 패킷 사유, 보드 status
    droneenv/bin/python drive_server.py -vv        # + 패킷마다 한 줄

모터보드 배터리 전압도 BATTERY_PERIOD_S 마다 읽어 MAVLink SYS_STATUS 로 만들어
127.0.0.1:14551 (`app.py stream`) 에 넘긴다. 지상국 주소는 그쪽만 알기 때문이다 —
stream 이 지상국으로 중계하고, 지상국은 BATTERY_COMPONENT 가 보낸 배터리값만 쓴다.
잔량 % 는 motor_link.BATTERY_CELLS(6S) 로 나눈 셀 전압을 LIPO_CURVE 에 대어 계산한다.
"""

from __future__ import annotations

import argparse
import socket
import struct
import time
from dataclasses import dataclass

from motor_link import CommError, MotorLink

PACKET = struct.Struct("<4sIhh")
MAGIC = b"DRV1"

# 클라이언트 주기(50ms)의 6배. 패킷 몇 개 유실은 버티고, 링크가 끊기면 금방 선다.
DEADMAN_S = 0.3

# -v 일 때 요약 줄을 찍는 주기.
REPORT_PERIOD_S = 1.0

# 배터리를 읽어 지상국으로 보내는 주기. 지상국 계기판 갱신(1Hz FC 텔레메트리)과 맞춘다.
BATTERY_PERIOD_S = 1.0
VEHICLE_SYSTEM_ID = 1
# 지상국 `aerover/src/core/telemetry/mavlink.py` 의 BATTERY_COMPONENT 와 같아야 한다.
BATTERY_COMPONENT = 180  # MAV_COMP_ID_BATTERY

# SYS_STATUS 는 손으로 MAVLink v1 프레임을 만든다. pymavlink 를 import 하면 droneenv 가 아닌
# 시스템 python(`python drive_server.py`)에선 ModuleNotFoundError 로 죽는다. 지상국은 v1 도 받는다.
MAVLINK_V1_STX = 0xFE
SYS_STATUS_ID = 1
SYS_STATUS_CRC_EXTRA = 124
# 필드는 MAVLink 규칙대로 크기순: present·enabled·health(u32) load·voltage(u16) current(i16)
# drop_rate·errors_comm·errors_count1~4(u16) battery_remaining(i8)
SYS_STATUS = struct.Struct("<IIIHHhHHHHHHb")

# LiPo 셀 전압(V) → 잔량(%). 무부하 기준 흔히 쓰는 곡선을 점으로 찍고 사이는 직선으로 잇는다.
# 주행 중엔 전압이 처져서 실제보다 낮게 나온다 — 정밀 잔량이 아니라 경고용이다.
LIPO_CURVE = (
    (3.27, 0), (3.61, 5), (3.69, 10), (3.73, 20), (3.77, 30), (3.80, 40),
    (3.84, 50), (3.87, 60), (3.95, 70), (4.02, 80), (4.11, 90), (4.20, 100),
)


def seq_newer(seq: int, last: int) -> bool:
    """u32 wrap-around 를 고려한 seq 비교. UDP 는 순서가 뒤바뀔 수 있다."""
    return 0 < ((seq - last) & 0xFFFFFFFF) < 0x80000000


def lipo_percent(cell_v: float) -> int:
    """셀 전압 → 잔량 %. 곡선 밖은 0 · 100 으로 자른다."""
    if cell_v <= LIPO_CURVE[0][0]:
        return 0
    for (v0, p0), (v1, p1) in zip(LIPO_CURVE, LIPO_CURVE[1:]):
        if cell_v <= v1:
            return round(p0 + (p1 - p0) * (cell_v - v0) / (v1 - v0))
    return 100


def x25_crc(data: bytes) -> int:
    """MAVLink 체크섬 (CRC-16/MCRF4XX)."""
    crc = 0xFFFF
    for b in data:
        tmp = (b ^ crc) & 0xFF
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def battery_sys_status(seq: int, mv: int, pct: int) -> bytes:
    """팩 전압(mV) · 잔량(%) → SYS_STATUS 프레임. 전류는 보드가 모르므로 MAVLink 의 "모름"(-1)."""
    payload = SYS_STATUS.pack(0, 0, 0, 0, mv, -1, 0, 0, 0, 0, 0, 0, pct)
    head = bytes([len(payload), seq & 0xFF, VEHICLE_SYSTEM_ID, BATTERY_COMPONENT, SYS_STATUS_ID])
    crc = x25_crc(head + payload + bytes([SYS_STATUS_CRC_EXTRA]))
    return bytes([MAVLINK_V1_STX]) + head + payload + struct.pack("<H", crc)


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} {msg}", flush=True)


@dataclass
class RxStats:
    """REPORT_PERIOD_S 한 구간의 카운터. 요약을 찍고 나면 새로 만든다."""
    accepted: int = 0
    lost: int = 0          # seq 가 건너뛴 개수 (UDP 유실 추정)
    bad_size: int = 0
    bad_magic: int = 0
    old_seq: int = 0       # 늦게 도착했거나 중복된 패킷
    motor_writes: int = 0
    write_ms_max: float = 0.0
    gap_ms_max: float = 0.0  # 연속 수신 패킷 사이 최대 간격


def main() -> int:
    p = argparse.ArgumentParser(description="UDP → MotorLink 드라이브 서버")
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--serial", default="/dev/ttyAMA5")
    p.add_argument("--battery-to", type=int, default=14551,
                   help="배터리 SYS_STATUS 를 보낼 127.0.0.1 포트 (app.py stream 의 로컬 중계)")
    p.add_argument("-v", "--verbose", action="count", default=0,
                   help="-v: 1초 요약 + 거부 사유 + 보드 status, -vv: 패킷마다 출력")
    a = p.parse_args()
    v = a.verbose

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((a.bind, a.port))
    sock.settimeout(0.05)
    batt_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    batt_dest = ("127.0.0.1", a.battery_to)
    batt_seq = 0

    with MotorLink(a.serial) as m:
        m.clear_err()
        log(f"listening udp {a.bind}:{a.port} → {a.serial} (verbose={v})")
        if v:
            log(f"board: {m.status()}")

        last_rx = float("-inf")
        last_seq: int | None = None
        cur: tuple[int, int] | None = (0, 0)  # None = 보드와 상태가 어긋남, 다음에 무조건 재전송
        linked = False
        stats = RxStats()
        next_report = time.monotonic() + REPORT_PERIOD_S
        next_battery = time.monotonic()
        batt_ok = True  # 오류 로그를 상태가 바뀔 때만 찍는다 — 1초마다 같은 줄이 쌓이지 않게

        while True:
            try:
                data, addr = sock.recvfrom(64)
            except socket.timeout:
                data = None
            now = time.monotonic()

            if data and len(data) != PACKET.size:
                stats.bad_size += 1
                if v:
                    log(f"reject: size {len(data)}B (want {PACKET.size}B) from {addr[0]}:{addr[1]}")
            elif data:
                magic, seq, da, db = PACKET.unpack(data)
                # 링크가 끊겼다 다시 붙은 경우(GUI 재시작 → seq 가 0 부터)엔 seq 를 따지지 않는다.
                stale = now - last_rx > DEADMAN_S
                if magic != MAGIC:
                    stats.bad_magic += 1
                    if v:
                        log(f"reject: magic {magic!r} from {addr[0]}:{addr[1]}")
                elif not (stale or last_seq is None or seq_newer(seq, last_seq)):
                    stats.old_seq += 1
                    if v:
                        log(f"reject: old seq {seq} (last {last_seq})")
                else:
                    if not stale and last_seq is not None:
                        skipped = ((seq - last_seq) & 0xFFFFFFFF) - 1
                        stats.lost += skipped
                        stats.gap_ms_max = max(stats.gap_ms_max, (now - last_rx) * 1000)
                        if v and skipped:
                            log(f"seq gap: {last_seq} → {seq} ({skipped} lost)")
                    stats.accepted += 1
                    if v >= 2:
                        log(f"rx seq={seq} A={da:+5d} B={db:+5d} from {addr[0]}:{addr[1]}"
                            f" dt={(now - last_rx) * 1000 if last_seq is not None else 0:.0f}ms")
                    last_rx, last_seq = now, seq
                    if not linked:
                        log(f"link up from {addr[0]}:{addr[1]} (seq={seq})")
                        linked = True
                    if (da, db) != cur:
                        t0 = time.monotonic()
                        try:
                            m.set_both(da, db)
                            cur = (da, db)
                            ms = (time.monotonic() - t0) * 1000
                            stats.motor_writes += 1
                            stats.write_ms_max = max(stats.write_ms_max, ms)
                            log(f"A={da:+5d} B={db:+5d}" + (f"  (set_both {ms:.1f}ms)" if v else ""))
                        except CommError as e:
                            log(f"motor error: {e} (set_both {da:+d},{db:+d})")
                            cur = None

            if linked and now - last_rx > DEADMAN_S:
                log(f"link lost → coast (no packet for {(now - last_rx) * 1000:.0f}ms, last seq={last_seq})")
                linked = False
                try:
                    m.coast()
                    cur = (0, 0)
                except CommError as e:
                    log(f"motor error: {e} (coast)")
                    cur = None

            if now >= next_battery:
                next_battery = now + BATTERY_PERIOD_S
                try:
                    b = m.battery()
                    if not batt_ok:
                        log("battery: read ok again")
                    batt_ok = True
                    if b.mv:  # 0 이면 보드가 아직 첫 샘플 전이다
                        pct = lipo_percent(b.cell_volts)
                        batt_sock.sendto(battery_sys_status(batt_seq, b.mv, pct), batt_dest)
                        batt_seq += 1
                        if v:
                            log(f"battery: {b}  → {pct}%")
                except CommError as e:
                    if batt_ok:
                        log(f"battery: read error: {e}")
                    batt_ok = False
                except OSError as e:  # 로컬 UDP 라 거의 없다. 있어도 주행은 계속한다
                    log(f"battery: send error: {e}")

            if v and now >= next_report:
                s = stats
                log(f"[{'LINK' if linked else 'idle'}] rx={s.accepted}/{REPORT_PERIOD_S:.0f}s lost={s.lost}"
                    f" rej(size={s.bad_size} magic={s.bad_magic} old={s.old_seq})"
                    f" gap_max={s.gap_ms_max:.0f}ms writes={s.motor_writes}"
                    f" write_max={s.write_ms_max:.1f}ms cur={cur}")
                try:
                    log(f"board: {m.status()}")
                except CommError as e:
                    log(f"board: status error: {e}")
                stats = RxStats()
                next_report = now + REPORT_PERIOD_S


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass  # with 블록을 빠져나오며 MotorLink.close() 가 coast 를 보낸다
