import contextlib
import struct
import time

from src.core.telemetry.crsf import build_frame
from src.core.telemetry.home import HomePoint
from src.core.telemetry.receiver import SerialReader
from src.core.telemetry.state import TelemetryStore, age, last_error
from src.core.telemetry.status import LinkJudge


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# ---- state ----

def test_store_snapshot_is_a_copy_and_records_rx_time():
    clock = Clock()
    store = TelemetryStore(clock)
    store.update("gps", {"sats": 7})
    snap = store.snapshot()
    snap["gps"]["sats"] = 0
    assert store.snapshot()["gps"]["sats"] == 7
    clock.t += 2.5
    assert age(store.snapshot(), "gps", now=clock()) == 2.5
    assert age(store.snapshot(), "battery", now=clock()) is None


def test_error_round_trip():
    store = TelemetryStore()
    assert last_error(store.snapshot()) is None
    store.set_error("SerialException: no port")
    assert last_error(store.snapshot()) == "SerialException: no port"


# ---- status ----

def _snap(clock: Clock, lq: int | None) -> dict:
    store = TelemetryStore(clock)
    if lq is not None:
        store.update("link", {"up_lq": lq})
    return store.snapshot()


def test_link_judge_transitions():
    clock = Clock()
    judge = LinkJudge(clock=clock)
    assert judge.status(_snap(clock, None)) == "NO_DATA"
    assert judge.status(_snap(clock, 90)) == "OK"
    assert judge.status(_snap(clock, 30)) == "WEAK"

    snap = _snap(clock, 0)                       # lq 0 — 아직은 WEAK
    assert judge.status(snap) == "WEAK"
    clock.t += 1.0                               # 1초 지속 → LOST (프레임은 계속 온다고 가정)
    snap["_rx_at"]["link"] = clock()
    assert judge.status(snap) == "LOST"

    assert judge.status(_snap(clock, 90)) == "OK"   # 회복

    stale = _snap(clock, 90)
    clock.t += 0.5                               # 프레임 자체가 끊김
    assert judge.status(stale) == "NO_DATA"


# ---- home ----

def _gps_snap(lat: float, lon: float, sats: int = 8) -> dict:
    store = TelemetryStore()
    store.update("gps", {"lat": lat, "lon": lon, "sats": sats})
    return store.snapshot()


def test_home_requires_fix_and_measures_offsets():
    home = HomePoint()
    assert home.set(_gps_snap(37.5, 127.0, sats=0)) is False
    assert not home.is_set
    assert home.distance(_gps_snap(37.5, 127.0)) is None

    assert home.set(_gps_snap(37.5, 127.0)) is True
    north = _gps_snap(37.5 + 100 / 111_320.0, 127.0)
    assert abs(home.distance(north) - 100) < 0.01
    assert abs(home.bearing(north)) < 1e-6

    east = _gps_snap(37.5, 127.0 + 0.001)
    assert abs(home.bearing(east) - 90) < 1e-6
    assert 80 < home.distance(east) < 90              # 위도 37.5° 에서 경도 0.001° ≈ 88 m

    home.clear()
    assert home.distance(north) is None


# ---- receiver ----

class FakePort:
    """한 번은 프레임을 주고, 그 뒤로는 빈 bytes (타임아웃) 를 준다."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, size: int) -> bytes:
        out, self._data = self._data[:size], self._data[size:]
        if not out:
            time.sleep(0.005)
        return out


def test_serial_reader_updates_store_and_reconnects_on_error():
    store = TelemetryStore()
    payload = struct.pack(">HH", 168, 12) + bytes([0, 0, 0, 77])
    opens = []

    @contextlib.contextmanager
    def open_port():
        opens.append(1)
        if len(opens) == 1:
            raise OSError("no such port")            # 첫 시도는 실패 → 재시도해야 한다
        yield FakePort(build_frame(0x08, payload))

    reader = SerialReader(store, open_port, retry_sec=0.0, sleep=lambda s: None)
    reader.start()
    deadline = time.time() + 2
    while time.time() < deadline and store.snapshot()["battery"]["voltage"] == 0.0:
        time.sleep(0.01)
    reader.stop()
    reader.join(1.0)

    snap = store.snapshot()
    assert snap["battery"]["voltage"] == 16.8
    assert snap["battery"]["remaining_pct"] == 77
    assert last_error(snap) is None                  # 두 번째 열기가 성공하며 오류가 지워졌다
    assert len(opens) >= 2


def test_no_data_threshold_follows_the_source():
    """파이 중계(MAVLink)는 수신 간격이 1~4초라 CRSF 기준(0.3초)을 쓰면 안 된다."""
    clock = Clock()
    judge = LinkJudge(clock=clock)

    store = TelemetryStore(clock)
    store.update("link", {"source": "mavlink", "up_lq": 90})
    snap = store.snapshot()
    clock.t += 2.0                               # CRSF 였으면 이미 NO_DATA
    assert judge.status(snap) == "OK"
    clock.t += 4.0                               # 5초를 넘기면 MAVLink 도 끊긴 것
    assert judge.status(snap) == "NO_DATA"


def test_crsf_keeps_the_strict_threshold():
    clock = Clock()
    judge = LinkJudge(clock=clock)

    store = TelemetryStore(clock)
    store.update("link", {"source": "crsf", "up_lq": 90})
    snap = store.snapshot()
    clock.t += 0.5
    assert judge.status(snap) == "NO_DATA"
