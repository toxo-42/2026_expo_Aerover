"""전송 바이트 포맷. 소켓을 모르는 순수 함수라 맥에서도 테스트할 수 있다."""
from __future__ import annotations

import struct
from typing import Iterator

# TCP: [4바이트 길이(big-endian)][JPEG 바이트] — aerover/core/link.py 가 이 포맷을 읽는다
LENGTH_FMT = "!I"

# UDP: 이미지ID(4) + 총청크수(2) + 청크인덱스(2) = 8바이트 헤더 + 청크
UDP_HEADER_FMT = "!IHH"
UDP_HEADER_SIZE = struct.calcsize(UDP_HEADER_FMT)


def length_prefixed(buf: bytes) -> bytes:
    return struct.pack(LENGTH_FMT, len(buf)) + buf


def chunk_count(size: int, chunk_size: int) -> int:
    return (size + chunk_size - 1) // chunk_size


def udp_chunks(img_id: int, data: bytes, chunk_size: int) -> Iterator[bytes]:
    total = chunk_count(len(data), chunk_size)
    for idx in range(total):
        chunk = data[idx * chunk_size: (idx + 1) * chunk_size]
        yield struct.pack(UDP_HEADER_FMT, img_id, total, idx) + chunk
