import copy
import math
import random
import threading
import time

USE_DUMMY = True  # 실제 환경에서는 False로 설정

""" 드론 텔레메트리 설정 (나중에 실제 기체에 맞게 수정해야함)"""
CELLS = 4  # 배터리 셀 수
CAPACITY_MAH = 2200  # 배터리 용량
HOVER_CURRENT_A = 18.0  # 호버링 시 소모 전류
HOME_LAT = 37.5665  # 이륙지점
HOME_LON = 126.9780
HOME_ALT_M = 38  # 이륙지점 고도
TARGET_AGL_M = 15  # 목표 고도

# 위도(Latitude) 1도 = 약 111.32 km, 경도(Longitude)는 위도에 따라 달라짐
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(HOME_LAT))

""" 공유 상태 저장공간 """
_lock = threading.Lock()

_state = {
    "gps": {
        "lat": 0.0,
        "lon": 0.0,
        "speed_kmh": 0.0,
        "heading": 0.0,
        "alt_m": 0,
        "sats": 0,
    },
    "battery": {
        "voltage": 0.0,
        "current": 0.0,
        "used_mah": 0,
        "remaining_pct": 0,
    },
    "link": {
        "up_rssi1": 0,
        "up_rssi2": 0,
        "up_lq": 0,
        "up_snr": 0,
        "antenna": 0,
        "rf_mode": 0,
        "tx_power_idx": 0,
        "down_rssi": 0,
        "down_lq": 0,
        "down_snr": 0,
    },
    "_rx_at": {"gps": None, "battery": None, "link": None},
}
# 키 이름과 개수를 radiomaster controller에서 보내는 데이터와 동일하게 맞춤

""" 더미 시뮬레이터 조정 파라미터 (디버딩용 ,실제 환경에서는 삭제)"""

_sim = {
    "t0": None,  # 시작 시각
    "last": None,  # 직전 갱신 시각
    "agl": 0.0,  # 지상고도 (m)
    "heading": 0.0,  # 진행 방향 (deg)
    "speed": 0.0,  # 대략 속도 (km/h)
    "lat": HOME_LAT,
    "lon": HOME_LON,
    "used_mah": 0.0,
    "next_gps": 0.0,  # 다음 프레임 도착 예정 시각
    "next_bat": 0.0,
    "next_link": 0.0,
}

""" dummy simulator logic """


def _advance(now):
    """경과 시간 만큼 더미 기체 움직임"""
    if _sim["t0"] is None:
        _sim["t0"] = now
        _sim["last"] = now
    dt = now - _sim["last"]
    if dt <= 0:
        return
    _sim["last"] = now
    elapsed = now - _sim["t0"]

    """ GPS fix: 처음 6초는 못 잡은 상태 (실제로도 fix까지 시간이 걸림) """
    sats = 0 if elapsed < 6 else min(14, 6 + int((elapsed - 6) / 2))

    """ 이륙 후 목표 고도 유지 시나리오.
        fix를 잡기 전에는 지상에 서 있어야 한다.
        (안 그러면 fix 잡는 순간 고도가 순간이동한다) """
    cilmbing = sats > 0 and _sim["agl"] < TARGET_AGL_M - 0.3
    if cilmbing:
        _sim["agl"] += 2.5 * dt  # 2.5 m/s 상승
    elif _sim["agl"] > 0.5:
        # 목표 고도로 되돌리며 흔들림 (되돌리는 항이 없으면 random walk로 흘러간다)
        _sim["agl"] += (
            (TARGET_AGL_M - _sim["agl"]) * 0.8 + random.uniform(-1.5, 1.5)
        ) * dt

    """ 방향 · 속도 조금씩 조정 (직전 값에서 이어가는 느낌).
        떠 있을 때만 움직인다 — 지상에서 속도가 잡히면 그것도 모순이다 """
    airborne = _sim["agl"] > 0.5
    if airborne:
        _sim["heading"] = (_sim["heading"] + random.uniform(-8, 8) * dt) % 360
        _sim["speed"] = max(
            0.0, min(30.0, _sim["speed"] + random.uniform(-3, 3) * dt)
        )  # 0~30 km/h 범위 유지

    """ 위치 유도 """
    moved_m = _sim["speed"] / 3.6 * dt  # m/s로 변환
    _sim["lat"] += moved_m * math.cos(math.radians(_sim["heading"])) / _M_PER_DEG_LAT
    _sim["lon"] += moved_m * math.sin(math.radians(_sim["heading"])) / _M_PER_DEG_LON

    """ 전류 소모량 계산 (상승중엔 더 먹음) """
    current = HOVER_CURRENT_A * (1.0 + 0.5 * cilmbing)
    current += random.uniform(-1.5, 1.5)  # 전류계 측정 노이즈
    _sim["used_mah"] += current * 1000 * dt / 3600  # A -> mAh 적분

    """ 프레임 생성 — 종류마다 도착 주기가 다르다 (TLM ratio 종속) """

    """ GPS (0x02) """
    if now >= _sim["next_gps"]:
        _sim["next_gps"] = now + 1.0
        with _lock:
            _state["gps"].update(
                lat=round(_sim["lat"], 7) if sats else 0.0,
                lon=round(_sim["lon"], 7) if sats else 0.0,
                speed_kmh=round(_sim["speed"], 1),
                heading=round(_sim["heading"], 2),
                alt_m=int(HOME_ALT_M + _sim["agl"]) if sats else 0,  # MSL 기준
                sats=sats,
            )
            _state["_rx_at"]["gps"] = now

    """ 배터리 (0x08) """
    if now >= _sim["next_bat"]:
        _sim["next_bat"] = now + 1.0
        used_ratio = min(1.0, _sim["used_mah"] / CAPACITY_MAH)
        v_rest = (4.2 - 0.7 * used_ratio) * CELLS  # 셀당 4.2V -> 3.5V
        with _lock:
            _state["battery"].update(
                voltage=round(v_rest - current * 0.010, 1),  # 전류에 의한 전압 강하
                current=round(current, 1),
                used_mah=int(_sim["used_mah"]),
                remaining_pct=int(round((1 - used_ratio) * 100)),
            )
            _state["_rx_at"]["battery"] = now

    """ 링크 (0x14) — 기체 전원과 무관하게 계속 옴 """
    if now >= _sim["next_link"]:
        _sim["next_link"] = now + 0.1
        dist = math.hypot(  # 멀어질수록 전파가 나빠지게
            (_sim["lat"] - HOME_LAT) * _M_PER_DEG_LAT,
            (_sim["lon"] - HOME_LON) * _M_PER_DEG_LON,
        )
        rssi = int(-40 - 25 * math.log10(1 + dist / 30))
        lq = 100 if rssi > -95 else max(0, 100 - (abs(rssi) - 95) * 8)
        with _lock:
            _state["link"].update(
                up_rssi1=rssi,
                up_rssi2=rssi - random.randint(0, 4),
                up_lq=lq,
                up_snr=random.randint(4, 10),
                antenna=0,
                rf_mode=6,
                tx_power_idx=3,
                down_rssi=rssi - 3,
                down_lq=max(0, lq - random.randint(0, 3)),
                down_snr=random.randint(2, 8),
            )
            _state["_rx_at"]["link"] = now


def _dummy_loop():
    """실제 CRSF 수신 스레드 자리. 프레임은 화면 갱신과 무관하게 계속 들어와야 한다"""
    while True:
        _advance(time.time())
        time.sleep(0.05)


if USE_DUMMY:
    threading.Thread(target=_dummy_loop, daemon=True).start()


""" 공개 API — 대시보드는 이 아래만 사용 """


def get_telemetry():
    """현재 텔레메트리 스냅샷 반환"""
    with _lock:
        return copy.deepcopy(_state)  # 그리는 도중 값이 바뀌지 않게 복사본


def age(snap, kind):
    """kind('gps'/'battery'/'link') 프레임을 마지막으로 받은 지 몇 초 됐는지.
    한 번도 못 받았으면 None"""
    ts = snap["_rx_at"].get(kind)
    return None if ts is None else time.time() - ts


_lq_zero_since = None  # up_lq가 0으로 떨어진 시각


def link_status(snap):
    """연결 판정
    "NO_DATA" — 프레임 자체가 300ms 이상 안 옴 (USB/핸드셋 문제)
    "LOST"    — up_lq 0 이 1초 이상 지속 (링크 끊김)
    "WEAK" / "OK"
    """
    global _lq_zero_since

    a = age(snap, "link")
    if a is None or a > 0.3:
        return "NO_DATA"

    lq = snap["link"]["up_lq"]
    if lq == 0:
        if _lq_zero_since is None:
            _lq_zero_since = time.time()
        return "LOST" if time.time() - _lq_zero_since >= 1.0 else "WEAK"

    _lq_zero_since = None
    return "WEAK" if lq < 60 else "OK"


def distance_from_home(snap):
    """이륙지점으로부터의 수평 거리 (m). fix 없으면 None"""
    g = snap["gps"]
    if not g["sats"]:
        return None
    return math.hypot(
        (g["lat"] - HOME_LAT) * _M_PER_DEG_LAT,
        (g["lon"] - HOME_LON) * _M_PER_DEG_LON,
    )


def bearing_from_home(snap):
    """이륙지점 기준 방위각 (deg, 북=0). fix 없으면 None"""
    g = snap["gps"]
    if not g["sats"]:
        return None
    north = (g["lat"] - HOME_LAT) * _M_PER_DEG_LAT
    east = (g["lon"] - HOME_LON) * _M_PER_DEG_LON
    return math.degrees(math.atan2(east, north)) % 360


""" 단독 테스트: python telemetry.py """

if __name__ == "__main__":
    while True:
        s = get_telemetry()
        g, b, k = s["gps"], s["battery"], s["link"]
        print(
            f"alt {g['alt_m']:4d}m  sats {g['sats']:2d}  "
            f"hdg {g['heading']:6.1f}  spd {g['speed_kmh']:4.1f}  |  "
            f"{b['voltage']:5.1f}V {b['current']:5.1f}A {b['remaining_pct']:3d}%  |  "
            f"LQ {k['up_lq']:3d}  RSSI {k['up_rssi1']:4d}  {link_status(s)}"
        )
        time.sleep(0.5)
