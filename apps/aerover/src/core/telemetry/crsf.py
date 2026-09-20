"""CRSF 프레임 파서 — 순수 함수. 기체 없이 바이트만으로 테스트한다.

    프레임: SYNC | LEN | TYPE | PAYLOAD | CRC8(DVB-S2, poly 0xD5)

SYNC 는 보내는 쪽 주소다. 어디서 받느냐에 따라 다르다:
  0xC8 = 비행컨트롤러 — FC 에 직결했을 때
  0xEA = 핸드셋      — 조종기 USB 텔레메트리 미러. **우리가 쓰는 경로**
2026-09-07 Radiomaster Pocket 실측: 6초에 195프레임이 전부 0xEA 였다.
0xC8 만 받으면 한 프레임도 안 통과한다. 그래서 둘 다 받는다.

프레임 종류를 추가하려면 파서 함수 하나를 쓰고 `FRAMES` 에 한 줄 넣는다.
`frames()` 는 손대지 않는다.

형식·실측값·함정은 `aerover/crsf-telemetry-sniffing.md` 3~5절.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Callable, Iterator

SYNC_FC = 0xC8
SYNC_HANDSET = 0xEA
SYNC = frozenset((SYNC_FC, SYNC_HANDSET))

MIN_LEN, MAX_LEN = 2, 62        # LEN 은 TYPE+PAYLOAD+CRC. 프레임 전체는 LEN+2


# ---- CRC8 DVB-S2 ----

def _make_crc_table(poly: int = 0xD5) -> bytes:
    table = bytearray(256)
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ poly) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        table[i] = c
    return bytes(table)


_CRC8_TABLE = _make_crc_table()


def crc8(data: bytes) -> int:
    c = 0
    for b in data:
        c = _CRC8_TABLE[c ^ b]
    return c


# ---- 페이로드 파서 (하나 = 프레임 종류 하나) ----

def _u24(b: bytes) -> int:
    return (b[0] << 16) | (b[1] << 8) | b[2]


def parse_gps(p: bytes) -> dict:
    lat, lon, spd, hdg, alt, sats = struct.unpack(">iiHHHB", p)
    return dict(lat=lat / 1e7, lon=lon / 1e7, speed_kmh=spd / 10,
                heading=hdg / 100, alt_m=alt - 1000, sats=sats)


def parse_battery(p: bytes) -> dict:
    v, a = struct.unpack(">HH", p[0:4])
    return dict(voltage=v / 10, current=a / 10,
                used_mah=_u24(p[4:7]), remaining_pct=p[7])


def parse_link(p: bytes) -> dict:
    # RSSI 는 **부호있는 int8 (dBm)** 이다. 스펙 문서는 "uint8 을 음수화"라고 하지만
    # 실측(2026-09-07 Radiomaster Pocket)은 235 → -21dBm. -p[0] 로 읽으면 -235dBm 이
    # 나오는데 그건 물리적으로 불가능한 값이다.
    r1, r2, dr = struct.unpack("bbb", bytes([p[0], p[1], p[7]]))
    return dict(source="crsf", up_rssi1=r1, up_rssi2=r2, up_lq=p[2],
                up_snr=struct.unpack("b", p[3:4])[0],
                antenna=p[4], rf_mode=p[5], tx_power_idx=p[6],
                down_rssi=dr, down_lq=p[8],
                down_snr=struct.unpack("b", p[9:10])[0])


def parse_attitude(p: bytes) -> dict:
    """roll/pitch/yaw. 단위는 라디안 * 10000 (int16).
    실측: 01 b4 fc de 29 73 → +2.5°, -4.6°, +60.8° (책상에 놓인 기체)."""
    r, pi, y = struct.unpack(">hhh", p)
    return dict(roll=math.degrees(r / 10000), pitch=math.degrees(pi / 10000),
                yaw=math.degrees(y / 10000) % 360)


def parse_baro_alt(p: bytes) -> dict:
    """기압 고도. uint16, 데시미터, +10000 오프셋 (즉 10000 = 0m).
    **GPS 와 무관하다** — 기압계는 FC 에 내장이라 GPS 모듈이 없어도 온다.
    이륙 지점 기준이 아니라 기압 기준이므로, 지상고도가 필요하면 이륙 전 값을
    빼야 한다. 실측은 실내 정지라 10000 고정이었고 **변화는 검증하지 못했다.**"""
    return dict(alt_m=(struct.unpack(">H", p)[0] - 10000) / 10)


def parse_vario(p: bytes) -> dict:
    """상승률. int16, cm/s. 실측은 정지 상태라 0 고정 — 변화 미검증."""
    return dict(vspeed_ms=struct.unpack(">h", p)[0] / 100)


def parse_mode(p: bytes) -> dict:
    """비행 모드. 널 종료 ASCII 문자열. 실측: b'!ERR\\x00' (arming 불가 상태)."""
    return dict(text=p.split(b"\x00")[0].decode("ascii", "replace"))


# ---- 프레임 종류 표 ----

@dataclass(frozen=True)
class FrameSpec:
    name: str                       # 상태 dict 의 키
    parse: Callable[[bytes], dict]
    length: int | None              # 고정 페이로드 길이. None 이면 가변(문자열)

    def accepts(self, payload: bytes) -> bool:
        if self.length is None:
            return bool(payload)
        return len(payload) == self.length


FRAMES: dict[int, FrameSpec] = {
    0x02: FrameSpec("gps", parse_gps, 15),
    0x07: FrameSpec("vario", parse_vario, 2),
    0x08: FrameSpec("battery", parse_battery, 8),
    0x09: FrameSpec("baro", parse_baro_alt, 2),
    0x14: FrameSpec("link", parse_link, 10),
    0x1E: FrameSpec("attitude", parse_attitude, 6),
    0x21: FrameSpec("mode", parse_mode, None),
}


# ---- 스트림 → 프레임 ----

def frames(buf: bytearray) -> Iterator[tuple[str, dict]]:
    """buf 에서 완성된 프레임을 꺼내 (종류, dict) 를 내놓는다.

    소비한 바이트는 buf 에서 지운다. 모르는 종류·길이가 안 맞는 프레임은 버린다.
    """
    while len(buf) >= 4:
        if buf[0] not in SYNC:
            del buf[0]
            continue
        ln = buf[1]
        if not (MIN_LEN <= ln <= MAX_LEN):
            del buf[0]
            continue
        if len(buf) < ln + 2:
            return                          # 아직 덜 받았다

        frame = bytes(buf[2:2 + ln])        # TYPE..CRC
        if crc8(frame[:-1]) != frame[-1]:
            del buf[0]                      # 싱크 오류 — 1바이트만 밀어야 진짜 시작점을 안 놓친다
            continue

        del buf[:ln + 2]
        spec = FRAMES.get(frame[0])
        payload = frame[1:-1]
        if spec is not None and spec.accepts(payload):
            yield spec.name, spec.parse(payload)


def build_frame(ftype: int, payload: bytes, sync: int = SYNC_HANDSET) -> bytes:
    """테스트·시뮬레이터용 — 페이로드를 프레임으로 감싼다. 파서와 같은 규약을 쓴다."""
    body = bytes([ftype]) + payload
    return bytes([sync, len(body) + 1]) + body + bytes([crc8(body)])
