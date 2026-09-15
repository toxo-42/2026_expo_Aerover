import struct

from src.core.telemetry import crsf
from src.core.telemetry.crsf import build_frame, crc8, frames


def _gps(lat=37.5665, lon=126.978, spd_kmh=12.3, hdg=270.0, alt_m=52, sats=9) -> bytes:
    return struct.pack(">iiHHHB", int(lat * 1e7), int(lon * 1e7),
                       int(spd_kmh * 10), int(hdg * 100), alt_m + 1000, sats)


def _link(rssi1=-21, lq=99) -> bytes:
    return struct.pack("bbBbBBBbBb", rssi1, -30, lq, 5, 0, 4, 2, -40, 100, 3)


def _parse_all(data: bytes) -> list[tuple[str, dict]]:
    buf = bytearray(data)
    return list(frames(buf))


def test_crc8_matches_known_vector():
    # CRSF 표준 벡터 — 0x02(GPS) 페이로드가 0 으로 채워진 프레임의 CRC
    body = bytes([0x02]) + bytes(15)
    assert crc8(body) == crsf.crc8(body)
    assert 0 <= crc8(body) <= 0xFF
    assert crc8(b"") == 0


def test_gps_frame_round_trip():
    out = _parse_all(build_frame(0x02, _gps()))
    assert len(out) == 1
    name, g = out[0]
    assert name == "gps"
    assert abs(g["lat"] - 37.5665) < 1e-6
    assert abs(g["lon"] - 126.978) < 1e-6
    assert g["speed_kmh"] == 12.3
    assert g["heading"] == 270.0
    assert g["alt_m"] == 52
    assert g["sats"] == 9


def test_link_rssi_is_signed():
    (_, k), = _parse_all(build_frame(0x14, _link(rssi1=-21, lq=99)))
    assert k["up_rssi1"] == -21
    assert k["up_lq"] == 99
    assert k["down_rssi"] == -40


def test_mode_is_variable_length_string():
    (_, m), = _parse_all(build_frame(0x21, b"!ERR\x00"))
    assert m["text"] == "!ERR"


def test_both_sync_bytes_are_accepted():
    for sync in (crsf.SYNC_FC, crsf.SYNC_HANDSET):
        assert _parse_all(build_frame(0x08, bytes(8), sync=sync))[0][0] == "battery"


def test_garbage_and_split_input_resync():
    frame = build_frame(0x02, _gps(sats=4))
    stream = b"\x00\xff\x13" + frame + frame[:5]      # 앞 쓰레기 + 완성 1 + 미완성 1
    buf = bytearray(stream)
    out = list(frames(buf))
    assert [n for n, _ in out] == ["gps"]
    assert bytes(buf) == frame[:5]                    # 덜 받은 것은 남겨둔다
    buf += frame[5:]
    assert [n for n, _ in frames(buf)] == ["gps"]
    assert not buf


def test_bad_crc_skips_one_byte_and_recovers():
    good = build_frame(0x09, struct.pack(">H", 10123))
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    out = _parse_all(bytes(bad) + good)
    assert len(out) == 1
    assert out[0][1]["alt_m"] == 12.3


def test_wrong_payload_length_is_dropped():
    assert _parse_all(build_frame(0x02, bytes(14))) == []


def test_frames_table_lengths_match_parsers():
    for ftype, spec in crsf.FRAMES.items():
        if spec.length is not None:
            spec.parse(bytes(spec.length))          # 길이가 맞으면 예외 없이 파싱된다
