"""RTP/JPEG — RFC 3550 RTP 헤더 + RFC 2435 JPEG 페이로드. 순수 바이트 로직.

파이(`pi_code/src/stream/rtpjpeg.py`)와 지상국(`aerover/src/core/rtpjpeg.py`)에 **같은 파일**이
있다. 한쪽을 바꾸면 다른 쪽도 바꾼다 (tests/test_mirror.py 가 대조한다).

보내는 쪽 — JPEG 한 장에서 헤더(DQT·SOF·DHT·SOS)를 벗기고 **스캔 데이터만** MTU 크기로 쪼개
RTP 패킷에 싣는다. 양자화 테이블은 프레임의 첫 패킷에 같이 실린다 (Q=255).
받는 쪽 — 같은 타임스탬프의 패킷을 모아 스캔 데이터를 잇고, **표준 허프만 테이블**로
JPEG 헤더를 다시 만든다. 패킷이 하나라도 빠진 프레임은 버린다 (UDP — 재전송 없음).

RFC 2435 의 제약: 베이스라인 JPEG · 3성분 · 8비트 · 4:2:0 또는 4:2:2 · 표준 허프만 테이블 ·
8비트 양자화 테이블 · 폭과 높이 2040 이하. picamera2 JpegEncoder · PIL · OpenCV 의 기본
출력(optimize 끔)이 여기 맞는다. 표준 도구(GStreamer `rtpjpegdepay`, VLC, ffmpeg)로도 받을 수 있다.
"""
from __future__ import annotations

import math
import random
import struct
import time
from dataclasses import dataclass, field
from typing import Callable

RTP_VERSION = 2
PAYLOAD_TYPE_JPEG = 26          # RFC 3551 정적 페이로드 타입
CLOCK_HZ = 90_000               # RTP 타임스탬프 단위
Q_INBAND = 255                  # 양자화 테이블을 프레임마다 같이 보낸다
MAX_DIMENSION = 2040            # 8픽셀 단위 1바이트
DEFAULT_MTU = 1400              # Wi-Fi MTU 1500 안에서 IP 단편화 없이

RTP_HEADER = struct.Struct("!BBHII")        # V·P·X·CC | M·PT | seq | timestamp | ssrc
JPEG_HEADER_SIZE = 8                        # type-specific(1) offset(3) type(1) q(1) w(1) h(1)
RESTART_HEADER = struct.Struct("!HH")       # interval | F·L·count
QTABLE_HEADER = struct.Struct("!BBH")       # mbz | precision | length

SOI, EOI = b"\xff\xd8", b"\xff\xd9"


class UnsupportedJpeg(ValueError):
    """RFC 2435 로 실을 수 없는 JPEG."""


# ---- 표준 허프만 테이블 (JPEG 규격 Annex K.3 — RFC 2435 부록 B 와 같다) ----

_LUM_DC = (bytes([0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]),
           bytes(range(12)))
_CHM_DC = (bytes([0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0]),
           bytes(range(12)))
_LUM_AC = (bytes([0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7D]), bytes([
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07,
    0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08, 0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1, 0xF0,
    0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
    0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
    0x6A, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7,
    0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5,
    0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2,
    0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8,
    0xF9, 0xFA]))
_CHM_AC = (bytes([0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77]), bytes([
    0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71,
    0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91, 0xA1, 0xB1, 0xC1, 0x09, 0x23, 0x33, 0x52, 0xF0,
    0x15, 0x62, 0x72, 0xD1, 0x0A, 0x16, 0x24, 0x34, 0xE1, 0x25, 0xF1, 0x17, 0x18, 0x19, 0x1A, 0x26,
    0x27, 0x28, 0x29, 0x2A, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
    0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
    0x69, 0x6A, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
    0x88, 0x89, 0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3, 0xA4, 0xA5,
    0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3,
    0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA,
    0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8,
    0xF9, 0xFA]))

# (클래스 0=DC 1=AC, 테이블 번호) → (코드 길이별 개수 16바이트, 심볼)
STANDARD_HUFFMAN = {(0, 0): _LUM_DC, (1, 0): _LUM_AC, (0, 1): _CHM_DC, (1, 1): _CHM_AC}


# ---- JPEG 해체 ----

@dataclass(frozen=True)
class JpegInfo:
    width: int
    height: int
    type: int                   # 0 = 4:2:0, 1 = 4:2:2 (재시작 마커 플래그 +64 는 패킷에서만)
    lqt: bytes                  # 휘도 양자화 테이블 64바이트 (지그재그 순서 그대로)
    cqt: bytes                  # 색차 양자화 테이블 64바이트
    dri: int                    # 재시작 간격. 0 이면 없음
    scan: bytes                 # SOS 다음부터 EOI 전까지


def parse_jpeg(data: bytes) -> JpegInfo:
    """JPEG 를 뜯어 RFC 2435 에 실을 조각만 남긴다. 제약에 안 맞으면 UnsupportedJpeg."""
    if data[:2] != SOI:
        raise UnsupportedJpeg("SOI 가 없다")

    qtables: dict[int, bytes] = {}
    sof: tuple | None = None
    dri = 0
    pos = 2
    scan_start = -1
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            raise UnsupportedJpeg(f"마커가 깨졌다 (offset {pos})")
        marker = data[pos + 1]
        if marker == 0xFF:                          # 채움 바이트
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:   # 길이 없는 마커
            pos += 2
            continue
        (length,) = struct.unpack_from("!H", data, pos + 2)
        seg = data[pos + 4:pos + 2 + length]

        if marker == 0xDB:
            _parse_dqt(seg, qtables)
        elif marker == 0xC4:
            _check_dht(seg)
        elif marker == 0xC0:
            sof = _parse_sof(seg)
        elif marker in (0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            raise UnsupportedJpeg("베이스라인(SOF0) JPEG 이어야 한다")
        elif marker == 0xDD:
            (dri,) = struct.unpack_from("!H", seg)
        elif marker == 0xDA:
            _check_sos(seg)
            scan_start = pos + 2 + length
            break
        pos += 2 + length

    if scan_start < 0 or sof is None:
        raise UnsupportedJpeg("SOS 또는 SOF0 가 없다")
    eoi = data.rfind(EOI)
    if eoi < scan_start:
        raise UnsupportedJpeg("EOI 가 없다")

    width, height, jtype, lq_id, cq_id = sof
    if lq_id not in qtables or cq_id not in qtables:
        raise UnsupportedJpeg("양자화 테이블이 없다")
    return JpegInfo(width, height, jtype, qtables[lq_id], qtables[cq_id], dri, data[scan_start:eoi])


def _parse_dqt(seg: bytes, out: dict[int, bytes]) -> None:
    i = 0
    while i < len(seg):
        precision, table_id = seg[i] >> 4, seg[i] & 0x0F
        if precision != 0:
            raise UnsupportedJpeg("양자화 테이블은 8비트여야 한다")
        out[table_id] = bytes(seg[i + 1:i + 65])
        i += 65


def _check_dht(seg: bytes) -> None:
    i = 0
    while i < len(seg):
        key = (seg[i] >> 4, seg[i] & 0x0F)
        counts = bytes(seg[i + 1:i + 17])
        n = sum(counts)
        symbols = bytes(seg[i + 17:i + 17 + n])
        if key in STANDARD_HUFFMAN and STANDARD_HUFFMAN[key] != (counts, symbols):
            raise UnsupportedJpeg("허프만 테이블이 표준이 아니다 — 인코더의 optimize 를 꺼야 한다")
        i += 17 + n


def _parse_sof(seg: bytes) -> tuple[int, int, int, int, int]:
    precision, height, width, ncomp = struct.unpack_from("!BHHB", seg)
    if precision != 8 or ncomp != 3:
        raise UnsupportedJpeg("8비트 3성분 JPEG 이어야 한다")
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise UnsupportedJpeg(f"폭·높이는 {MAX_DIMENSION} 이하여야 한다")
    comps = [(seg[6 + 3 * k], seg[7 + 3 * k], seg[8 + 3 * k]) for k in range(3)]   # (id, HV, Tq)
    sampling = {0x22: 0, 0x21: 1}.get(comps[0][1])
    if sampling is None or comps[1][1] != 0x11 or comps[2][1] != 0x11:
        raise UnsupportedJpeg("4:2:0 또는 4:2:2 서브샘플링이어야 한다")
    if comps[1][2] != comps[2][2]:
        raise UnsupportedJpeg("Cb 와 Cr 은 같은 양자화 테이블을 써야 한다")
    return width, height, sampling, comps[0][2], comps[1][2]


def _check_sos(seg: bytes) -> None:
    if seg[0] != 3 or seg[2] != 0x00 or seg[4] != 0x11 or seg[6] != 0x11:
        raise UnsupportedJpeg("SOS 의 허프만 테이블 배정이 표준(Y:0, Cb·Cr:1)이 아니다")


# ---- JPEG 재조립 ----

def build_jpeg(width: int, height: int, jtype: int, lqt: bytes, cqt: bytes,
               dri: int, scan: bytes) -> bytes:
    """RFC 2435 부록 B 와 같은 순서로 헤더를 만들고 스캔 데이터를 붙인다."""
    out = bytearray(SOI)
    out += b"\xff\xdb" + struct.pack("!H", 2 + 65 * 2) + b"\x00" + lqt + b"\x01" + cqt
    out += b"\xff\xc0" + struct.pack("!HBHHB", 17, 8, height, width, 3)
    out += bytes([1, 0x22 if jtype == 0 else 0x21, 0, 2, 0x11, 1, 3, 0x11, 1])
    for (cls, tid), (counts, symbols) in STANDARD_HUFFMAN.items():
        out += b"\xff\xc4" + struct.pack("!HB", 3 + 16 + len(symbols), (cls << 4) | tid)
        out += counts + symbols
    if dri:
        out += b"\xff\xdd" + struct.pack("!HH", 4, dri)
    out += b"\xff\xda" + struct.pack("!HB", 12, 3) + bytes([1, 0x00, 2, 0x11, 3, 0x11, 0, 63, 0])
    out += scan
    if not scan.endswith(EOI):
        out += EOI
    return bytes(out)


# ---- 패킷화 ----

def packetize(info: JpegInfo, seq: int, timestamp: int, ssrc: int,
              mtu: int = DEFAULT_MTU) -> list[bytes]:
    """JPEG 한 장 → RTP 패킷 목록. 마지막 패킷에 마커 비트가 선다."""
    jtype = info.type + (64 if info.dri else 0)
    w8, h8 = math.ceil(info.width / 8), math.ceil(info.height / 8)
    scan, total = info.scan, len(info.scan)
    if total >= 1 << 24:
        raise UnsupportedJpeg("스캔 데이터가 16MB 를 넘는다")

    packets: list[bytes] = []
    offset = 0
    while True:
        header = struct.pack("!I", offset) + bytes([jtype, Q_INBAND, w8, h8])
        if info.dri:
            header += RESTART_HEADER.pack(info.dri, 0xFFFF)       # F=1 L=1 count=0x3FFF
        if offset == 0:
            tables = info.lqt + info.cqt
            header += QTABLE_HEADER.pack(0, 0, len(tables)) + tables
        room = mtu - RTP_HEADER.size - len(header)
        if room <= 0:
            raise ValueError(f"MTU {mtu} 가 너무 작다")
        chunk = scan[offset:offset + room]
        last = offset + len(chunk) >= total
        rtp = RTP_HEADER.pack(RTP_VERSION << 6, (0x80 if last else 0) | PAYLOAD_TYPE_JPEG,
                              seq & 0xFFFF, timestamp & 0xFFFFFFFF, ssrc & 0xFFFFFFFF)
        packets.append(rtp + header + chunk)
        seq += 1
        offset += len(chunk)
        if last:
            return packets


class RtpJpegPacketizer:
    """프레임마다 시퀀스·타임스탬프를 이어 붙이는 상태를 가진다. 소켓은 모른다."""

    def __init__(self, ssrc: int | None = None, mtu: int = DEFAULT_MTU,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ssrc = random.getrandbits(32) if ssrc is None else ssrc
        self.mtu = mtu
        self.seq = random.getrandbits(16)
        self._clock = clock
        self.frames = 0

    def packets(self, jpeg: bytes) -> list[bytes]:
        info = parse_jpeg(jpeg)
        timestamp = int(self._clock() * CLOCK_HZ) & 0xFFFFFFFF
        out = packetize(info, self.seq, timestamp, self.ssrc, self.mtu)
        self.seq = (self.seq + len(out)) & 0xFFFF
        self.frames += 1
        return out


# ---- 재조립 ----

@dataclass
class _Frame:
    jtype: int
    width: int
    height: int
    dri: int
    lqt: bytes | None = None
    cqt: bytes | None = None
    parts: dict[int, bytes] = field(default_factory=dict)     # 오프셋 → 조각
    end: int | None = None                                    # 마커 패킷이 알려준 총 길이

    def complete(self) -> bool:
        if self.end is None or self.lqt is None:
            return False
        pos = 0
        for off in sorted(self.parts):
            if off > pos:
                return False                    # 빠진 조각
            pos = max(pos, off + len(self.parts[off]))
        return pos == self.end

    def jpeg(self) -> bytes:
        scan = b"".join(self.parts[off] for off in sorted(self.parts))
        return build_jpeg(self.width, self.height, self.jtype, self.lqt, self.cqt, self.dri, scan)


@dataclass
class DepacketizerStats:
    packets: int = 0
    frames: int = 0             # 완성한 프레임
    dropped: int = 0            # 조각이 빠져 버린 프레임
    ignored: int = 0            # RTP 가 아니거나 지원하지 않는 패킷


class RtpJpegDepacketizer:
    """패킷을 하나씩 넣으면 프레임이 완성될 때 JPEG 를 돌려준다.

    동시에 최대 `keep` 개 프레임을 조립한다. 그보다 오래된 미완성 프레임은 버린다 —
    UDP 는 순서가 바뀌거나 빠질 수 있고, 실시간에서는 기다리는 것보다 버리는 게 맞다.
    """

    def __init__(self, keep: int = 2) -> None:
        self.keep = keep
        self.stats = DepacketizerStats()
        self._frames: dict[int, _Frame] = {}
        self._order: list[int] = []

    def push(self, packet: bytes) -> bytes | None:
        parsed = self._parse(packet)
        if parsed is None:
            self.stats.ignored += 1
            return None
        self.stats.packets += 1
        timestamp, marker, offset, jtype, width, height, dri, tables, data = parsed

        frame = self._frames.get(timestamp)
        if frame is None:
            frame = self._frames[timestamp] = _Frame(jtype, width, height, dri)
            self._order.append(timestamp)
            self._purge()
        if tables is not None:
            frame.lqt, frame.cqt = tables
        frame.parts[offset] = data
        if marker:
            frame.end = offset + len(data)

        if not frame.complete():
            return None
        # 이 프레임보다 먼저 시작해 아직 미완성인 것은 이제 못 쓴다 — 실시간이라 기다리지 않는다
        idx = self._order.index(timestamp)
        for old in self._order[:idx]:
            self._frames.pop(old, None)
            self.stats.dropped += 1
        del self._order[:idx + 1]
        self._frames.pop(timestamp, None)
        self.stats.frames += 1
        return frame.jpeg()

    # ---- 내부 ----

    @staticmethod
    def _parse(packet: bytes):
        if len(packet) < RTP_HEADER.size:
            return None
        b0, b1, _seq, timestamp, _ssrc = RTP_HEADER.unpack_from(packet)
        if b0 >> 6 != RTP_VERSION or (b1 & 0x7F) != PAYLOAD_TYPE_JPEG:
            return None
        pos = RTP_HEADER.size + 4 * (b0 & 0x0F)             # CSRC 목록
        if b0 & 0x10:                                       # 확장 헤더
            (_, ext_len) = struct.unpack_from("!HH", packet, pos)
            pos += 4 + 4 * ext_len
        if b0 & 0x20:                                       # 패딩
            packet = packet[:-packet[-1]]
        marker = bool(b1 & 0x80)

        payload = packet[pos:]
        if len(payload) < JPEG_HEADER_SIZE:
            return None
        (offset_word,) = struct.unpack_from("!I", payload)
        offset = offset_word & 0xFFFFFF
        jtype, q, w8, h8 = payload[4:8]
        p = JPEG_HEADER_SIZE
        dri = 0
        if jtype >= 64:
            dri, _ = RESTART_HEADER.unpack_from(payload, p)
            p += RESTART_HEADER.size
        if (jtype & 0x3F) not in (0, 1):
            return None
        tables = None
        if q >= 128:
            if offset == 0:
                mbz, precision, length = QTABLE_HEADER.unpack_from(payload, p)
                p += QTABLE_HEADER.size
                if precision != 0 or length not in (64, 128):
                    return None
                raw = payload[p:p + length]
                p += length
                tables = (raw[:64], raw[64:] or raw[:64])
        else:
            return None                                     # Q<128 의 기본 테이블은 지원하지 않는다
        return timestamp, marker, offset, jtype & 0x3F, w8 * 8, h8 * 8, dri, tables, payload[p:]

    def _purge(self) -> None:
        while len(self._order) > self.keep:
            old = self._order[0]
            if not self._frames[old].complete():
                self.stats.dropped += 1
            self._forget(old)

    def _forget(self, timestamp: int) -> None:
        self._frames.pop(timestamp, None)
        if timestamp in self._order:
            self._order.remove(timestamp)
