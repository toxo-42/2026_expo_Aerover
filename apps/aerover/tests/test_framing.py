import pytest

from src.core.framing import HEADER, FrameReader, length_prefixed


class ChunkedRecv:
    """recv(n) 을 흉내 낸다 — 요청보다 작은 조각으로 돌려주다가 끝나면 빈 bytes."""

    def __init__(self, data: bytes, chunk: int) -> None:
        self.data = data
        self.chunk = chunk
        self.pos = 0

    def __call__(self, n: int) -> bytes:
        take = min(n, self.chunk, len(self.data) - self.pos)
        out = self.data[self.pos:self.pos + take]
        self.pos += take
        return out


def test_reads_consecutive_frames_from_small_chunks():
    payloads = [b"a" * 10, b"b" * 3, b"c" * 1000]
    stream = b"".join(length_prefixed(p) for p in payloads)
    reader = FrameReader(ChunkedRecv(stream, chunk=7))
    assert [reader.read() for _ in range(3)] == payloads
    assert reader.read() is None                      # EOF


def test_eof_in_the_middle_of_a_frame_returns_none():
    stream = length_prefixed(b"x" * 50)[:20]
    assert FrameReader(ChunkedRecv(stream, chunk=64)).read() is None


def test_absurd_length_raises():
    reader = FrameReader(ChunkedRecv(HEADER.pack(1 << 31), chunk=4))
    with pytest.raises(ValueError):
        reader.read()


def test_recv_returning_none_means_stop():
    assert FrameReader(lambda n: None).read() is None
