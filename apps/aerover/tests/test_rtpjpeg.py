import io
import random

import cv2
import numpy as np
import pytest
from PIL import Image

from src.core.rtpjpeg import (DEFAULT_MTU, RTP_HEADER, RtpJpegDepacketizer, RtpJpegPacketizer,
                              UnsupportedJpeg, packetize, parse_jpeg)


def _image(w: int = 160, h: int = 120, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)


def _pil_jpeg(arr: np.ndarray, **kw) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=kw.pop("quality", 85), **kw)
    return buf.getvalue()


def _decode(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"))


def _round_trip(jpeg: bytes, mtu: int = DEFAULT_MTU, shuffle: bool = False) -> bytes | None:
    packets = RtpJpegPacketizer(ssrc=7, mtu=mtu, clock=lambda: 1.0).packets(jpeg)
    if shuffle:
        random.Random(1).shuffle(packets)
    depack = RtpJpegDepacketizer()
    out = None
    for p in packets:
        got = depack.push(p)
        if got is not None:
            out = got
    return out


def test_round_trip_reproduces_identical_pixels():
    jpeg = _pil_jpeg(_image())
    rebuilt = _round_trip(jpeg)
    assert rebuilt is not None
    assert np.array_equal(_decode(rebuilt), _decode(jpeg))     # 스캔 데이터가 그대로라 픽셀까지 같다


def test_422_subsampling_is_type_1():
    jpeg = _pil_jpeg(_image(), subsampling="4:2:2")
    assert parse_jpeg(jpeg).type == 1
    assert np.array_equal(_decode(_round_trip(jpeg)), _decode(jpeg))


def test_restart_markers_are_carried():
    ok, buf = cv2.imencode(".jpg", _image(), [cv2.IMWRITE_JPEG_QUALITY, 80,
                                               cv2.IMWRITE_JPEG_RST_INTERVAL, 4])
    assert ok
    jpeg = buf.tobytes()
    info = parse_jpeg(jpeg)
    assert info.dri == 4
    packets = packetize(info, 0, 0, 1)
    assert packets[0][RTP_HEADER.size + 4] >= 64                # 타입에 재시작 플래그
    assert np.array_equal(_decode(_round_trip(jpeg)), _decode(jpeg))


def test_packets_respect_mtu_and_mark_last():
    jpeg = _pil_jpeg(_image(320, 240))
    packets = RtpJpegPacketizer(mtu=600).packets(jpeg)
    assert len(packets) > 3
    assert all(len(p) <= 600 for p in packets)
    markers = [bool(p[1] & 0x80) for p in packets]
    assert markers == [False] * (len(packets) - 1) + [True]
    seqs = [int.from_bytes(p[2:4], "big") for p in packets]
    assert seqs == [(seqs[0] + i) & 0xFFFF for i in range(len(seqs))]
    assert all(p[1] & 0x7F == 26 for p in packets)


def test_out_of_order_packets_still_assemble():
    jpeg = _pil_jpeg(_image(320, 240))
    assert _round_trip(jpeg, mtu=500, shuffle=True) is not None


def test_lost_packet_drops_that_frame_only():
    a, b = _pil_jpeg(_image(seed=1)), _pil_jpeg(_image(seed=2))
    pk = RtpJpegPacketizer(mtu=400, clock=lambda: 1.0)
    packets_a = pk.packets(a)
    pk._clock = lambda: 2.0
    packets_b = pk.packets(b)
    del packets_a[len(packets_a) // 2]                         # 한 조각 유실

    depack = RtpJpegDepacketizer()
    got = [depack.push(p) for p in packets_a + packets_b]
    frames = [g for g in got if g is not None]
    assert len(frames) == 1 and np.array_equal(_decode(frames[0]), _decode(b))
    assert depack.stats.frames == 1 and depack.stats.dropped == 1


def test_non_rtp_and_wrong_payload_type_are_ignored():
    depack = RtpJpegDepacketizer()
    assert depack.push(b"\x00" * 30) is None
    packet = bytearray(RtpJpegPacketizer().packets(_pil_jpeg(_image()))[0])
    packet[1] = (packet[1] & 0x80) | 96                        # 다른 페이로드 타입
    assert depack.push(bytes(packet)) is None
    assert depack.stats.ignored == 2


def test_optimized_huffman_tables_are_rejected():
    with pytest.raises(UnsupportedJpeg, match="허프만"):
        parse_jpeg(_pil_jpeg(_image(), optimize=True))


def test_progressive_and_oversize_are_rejected():
    with pytest.raises(UnsupportedJpeg, match="베이스라인"):
        parse_jpeg(_pil_jpeg(_image(), progressive=True))
    with pytest.raises(UnsupportedJpeg, match="2040"):
        parse_jpeg(_pil_jpeg(_image(2048, 16)))
    with pytest.raises(UnsupportedJpeg):
        parse_jpeg(b"not a jpeg")


def test_pi_resolution_fits_in_header():
    info = parse_jpeg(_pil_jpeg(_image(1280, 960)))
    p = packetize(info, 0, 0, 1)[0]
    assert p[RTP_HEADER.size + 6] == 160 and p[RTP_HEADER.size + 7] == 120
