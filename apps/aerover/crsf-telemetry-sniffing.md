# RadioMaster Pocket → PC: CRSF 텔레메트리로 GPS / 배터리 / 링크 상태 읽기

목표: 핸드셋 USB(VCP)로 나오는 CRSF 텔레메트리 스트림을 PC에서 파싱해 GPS, 배터리, 링크 품질 3가지를 얻는다.
MSP는 사용하지 않는다. 이 3가지는 전부 CRSF 텔레메트리 프레임에 이미 들어있다.

> 2026-09-07 · 09-12 실측(Radiomaster Pocket + SpeedyBee F405 V4 INAV)과 다른 부분을 고쳤다. 코드 정본은 `aerover/src/core/telemetry/crsf.py`.

---

## 0. 전체 경로

```
FC ──CRSF(UART)── ELRS RX ──2.4GHz── Pocket 내부 ELRS 모듈
                                          │
                                    EdgeTX (Telemetry Mirror)
                                          │
                                     USB-VCP (COMx)
                                          │
                                      PC 파서
```

핸드셋은 중계만 한다. 프레임 내용을 만드는 건 FC이므로, FC 쪽 텔레메트리 설정이 먼저 맞아야 한다.

---

## 1. FC 쪽 준비

### 공통
- ELRS RX가 FC의 UART에 full-duplex, uninverted로 연결되어 있어야 한다.
- Receiver: `Serial (via UART)` + Serial Receiver Provider = `CRSF`
- CLI에서 확인: `get serialrx` → `serialrx_inverted = OFF`, `serialrx_halfduplex = OFF`

### Betaflight
- Receiver 탭에서 **Telemetry 활성화** (이게 꺼져 있으면 프레임이 아예 안 올라온다)
- Ports 탭: RX가 붙은 UART는 `Serial RX`만 ON. 같은 줄의 Telemetry Output(SmartPort 등)은 건드리지 않는다. CRSF는 같은 UART로 텔레메트리를 되돌려 보낸다.

### INAV
- Configuration 탭 → Telemetry ON
- GPS를 쓰려면 Configuration에서 GPS feature ON + GPS UART/보레이트 설정

### 항목별 전제조건
| 얻고 싶은 값 | 필요한 것 |
|---|---|
| GPS (0x02) | GPS 모듈 연결 + fix 확보. fix 전에는 lat/lon 0, sats 0으로 프레임만 온다. **INAV 는 GPS feature 가 켜져 있으면 모듈이 없어도 0 으로 보낸다** (09-12 모듈 분리 상태 실측, INAV `telemetry/crsf.c` 가 `feature(FEATURE_GPS)` 로 스케줄) |
| 배터리 (0x08) | 전압 센서 스케일 캘리브레이션. 전류계 없으면 current/mAh는 0 |
| 링크 (0x14) | 별도 설정 없음. ELRS 모듈이 항상 생성 |

> 0x14는 FC가 아니라 TX 모듈이 만든다. 그래서 기체 전원이 없어도 계속 나온다. 1번 항목과 헷갈리지 않게 주의.

---

## 2. Pocket(EdgeTX) 쪽 설정

1. `SYS` 버튼 → **Hardware** 페이지
2. 하단 시리얼 포트 목록에서 **VCP** 항목을 찾아 `Telemetry Mirror` 로 지정
   - 선택지에 없으면 EdgeTX 버전 문제다. 2.9 이상으로 올리거나, AUX1/AUX2(내부 UART, 배터리 커버 안쪽 패드)에 지정 후 USB-UART 어댑터를 쓴다
   - AUX 포트를 쓸 때만 보레이트를 지정한다(115200 권장). VCP는 USB CDC라 보레이트 무의미
3. 모델 설정 → 텔레메트리 페이지에서 `Discover new sensors` 를 한 번 돌려 센서가 실제로 잡히는지 확인
   - 여기서 GPS, RxBt, RQly 등이 안 보이면 PC 파싱도 당연히 안 된다. 여기서 먼저 해결
4. USB 케이블 연결 → 모드 선택 팝업에서 **USB Serial (VCP)** 선택
   - `USB Storage`를 고르면 라디오 동작이 멈춘다. 반드시 Serial
   - VCP 모드에서는 RF 송신이 정상 유지된다

### 포트 확인
- Windows: 장치 관리자 → 포트에 `STMicroelectronics Virtual COM Port` (COMx)
- Linux: `/dev/ttyACM0`, 권한 필요하면 `sudo usermod -aG dialout $USER`
- macOS: `/dev/tty.usbmodem*`

---

## 3. CRSF 프레임 포맷

```
+------+-----+------+-----------------+------+
| 0xC8 | LEN | TYPE |    PAYLOAD      | CRC8 |
+------+-----+------+-----------------+------+
```

- `0xC8` / `0xEA`: sync (보내는 쪽 주소). FC 에 직결하면 `0xC8`, **핸드셋 USB 미러는 `0xEA`** 다. 미러 실측은 전부 `0xEA` 였고 `0xC8` 만 받으면 한 프레임도 안 통과한다. 둘 다 받는다
- `LEN`: TYPE + PAYLOAD + CRC 의 바이트 수 → 프레임 총 길이는 `LEN + 2`
- `CRC8`: poly `0xD5` (DVB-S2), init 0. **TYPE + PAYLOAD 구간만** 계산
- 최대 프레임 64바이트, 바이트 스터핑 없음
- 모든 다중바이트 필드는 **빅엔디안**

스트림 중간에 붙으면 싱크가 안 맞으므로, sync 탐색 → LEN 범위 검사 → CRC 검증 3단계로 리싱크한다.

---

## 4. 필요한 3개 프레임

### 0x02 — GPS (payload 15 B)

| offset | 타입 | 필드 | 변환 |
|---|---|---|---|
| 0 | int32 | latitude | `/1e7` → deg |
| 4 | int32 | longitude | `/1e7` → deg |
| 8 | uint16 | groundspeed | `/10` → km/h |
| 10 | uint16 | heading | `/100` → deg |
| 12 | uint16 | altitude | `-1000` → m (MSL) |
| 14 | uint8 | satellites | 개 |

### 0x08 — Battery sensor (payload 8 B)

| offset | 타입 | 필드 | 변환 |
|---|---|---|---|
| 0 | uint16 | voltage | `/10` → V |
| 2 | uint16 | current | `/10` → A |
| 4 | uint24 | used capacity | mAh |
| 7 | uint8 | remaining | % |

### 0x14 — Link statistics (payload 10 B)

| offset | 타입 | 필드 | 변환 |
|---|---|---|---|
| 0 | int8 | uplink RSSI ant1 | dBm (그대로) |
| 1 | int8 | uplink RSSI ant2 | dBm (그대로) |
| 2 | uint8 | **uplink LQ** | % |
| 3 | int8 | uplink SNR | dB |
| 4 | uint8 | active antenna | 0/1 |
| 5 | uint8 | RF mode | 패킷레이트 인덱스 |
| 6 | uint8 | uplink TX power | 파워 인덱스 |
| 7 | int8 | downlink RSSI | dBm (그대로) |
| 8 | uint8 | downlink LQ | % |
| 9 | int8 | downlink SNR | dB |

- **uplink** = 기체가 조종 신호를 받는 품질 → "연결 상태"로 쓸 값
- **downlink** = 핸드셋이 텔레메트리를 받는 품질
- **RSSI 는 부호있는 int8 이다.** "uint8 을 음수화"로 읽으면 실측 `eb` → -235dBm 이라는 불가능한 값이 나온다. int8 로 읽으면 -21dBm (옆에 둔 기체의 정상값)

### 그 밖에 오는 프레임 (실측)

INAV 는 위 3개 말고도 보낸다. 10초 동안 받은 수: RADIO_ID 59 · LINK_STATS 49 · GPS·BATTERY·ATTITUDE·FLIGHT_MODE 각 5 · VARIO·BARO_ALT 각 4 (09-12).

| TYPE | 이름 | payload | 변환 | 실측 |
|---|---|---|---|---|
| 0x1E | ATTITUDE | int16 ×3 (roll, pitch, yaw) | 라디안 ×10000 | `01 b4 fc de 29 73` → +2.5° −4.6° +60.8° |
| 0x09 | BARO_ALT | uint16 | `(v − 10000) / 10` → m. **GPS 와 무관** (FC 내장 기압계) | `27 10` → 0m |
| 0x07 | VARIO | int16 | cm/s | `00 00` |
| 0x21 | FLIGHT_MODE | 널 종료 ASCII (가변 길이) | 문자열 | `!ERR` (09-07), `WAIT` (09-12) |
| 0x3A | RADIO_ID | — | ELRS 모듈 → 핸드셋 타이밍 동기. 파싱 안 함 | |

FLIGHT_MODE 는 비무장(disarmed)일 때 INAV `crsfFrameFlightMode` 가 이 순서로 고른다: GPS feature ON + `nav_extra_arming_safety` + fix 없음 → `WAIT`, 아니면 arming 불가 → `!ERR`, 아니면 `OK`. 즉 `WAIT` 는 `!ERR` 을 가린다.

---

## 5. 파서

```python
import struct, serial

POLY = 0xD5

CRC8 = []
for i in range(256):
    c = i
    for _ in range(8):
        c = ((c << 1) ^ POLY) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    CRC8.append(c)

def crc8(data):
    c = 0
    for b in data:
        c = CRC8[c ^ b]
    return c

def u24(b):
    return (b[0] << 16) | (b[1] << 8) | b[2]

def parse_gps(p):
    lat, lon, spd, hdg, alt, sats = struct.unpack('>iiHHHB', p)
    return dict(lat=lat/1e7, lon=lon/1e7, speed_kmh=spd/10,
                heading=hdg/100, alt_m=alt-1000, sats=sats)

def parse_battery(p):
    v, a = struct.unpack('>HH', p[0:4])
    return dict(voltage=v/10, current=a/10,
                used_mah=u24(p[4:7]), remaining_pct=p[7])

def parse_link(p):
    r1, r2, dr = struct.unpack('bbb', bytes([p[0], p[1], p[7]]))  # RSSI 는 int8
    return dict(up_rssi1=r1, up_rssi2=r2, up_lq=p[2],
                up_snr=struct.unpack('b', p[3:4])[0],
                antenna=p[4], rf_mode=p[5], tx_power_idx=p[6],
                down_rssi=dr, down_lq=p[8],
                down_snr=struct.unpack('b', p[9:10])[0])

HANDLERS = {
    0x02: ('gps',     15, parse_gps),
    0x08: ('battery',  8, parse_battery),
    0x14: ('link',    10, parse_link),
}

def frames(buf):
    """buf: bytearray. 소비한 바이트는 제거하고 (name, dict) 를 yield"""
    while len(buf) >= 4:
        if buf[0] not in (0xC8, 0xEA):  # FC 직결 / 핸드셋 USB 미러
            del buf[0]
            continue
        ln = buf[1]
        if not (2 <= ln <= 62):
            del buf[0]
            continue
        if len(buf) < ln + 2:
            return                      # 더 받아야 함
        frame = bytes(buf[2:2 + ln])    # TYPE..CRC
        if crc8(frame[:-1]) != frame[-1]:
            del buf[0]                  # 싱크 오류 → 1바이트 밀고 재시도
            continue
        ftype, payload = frame[0], frame[1:-1]
        del buf[:ln + 2]
        h = HANDLERS.get(ftype)
        if h and len(payload) == h[1]:
            yield h[0], h[2](payload)

def main(port='COM5'):
    buf = bytearray()
    with serial.Serial(port, 115200, timeout=0.1) as ser:
        while True:
            chunk = ser.read(256)
            if chunk:
                buf += chunk
                for name, data in frames(buf):
                    print(name, data)

if __name__ == '__main__':
    main()
```

`pip install pyserial` 필요. 포트 이름만 환경에 맞게 바꾼다.

---

## 6. 검증 순서

막히면 아래 순서로 좁힌다.

1. **바이트가 들어오는가** — 터미널(PuTTY 등)로 열어 쓰레기 문자라도 계속 흐르는지 본다. 안 흐르면 EdgeTX 시리얼 포트 설정 또는 USB 모드 문제
2. **CRC 통과 프레임이 잡히는가** — 파서에서 CRC 통과 프레임 카운터를 찍어본다. 0이면 먼저 **sync 를 `0xC8` 만 받고 있지 않은지** 본다 (09-07 에 실제로 이것 때문에 0 이었다 — 미러는 `0xEA`). 그다음이 Telemetry Mirror가 아닌 다른 모드(CLI/Debug)로 잡혀 있을 가능성
3. **0x14는 오는가** — 기체 전원 없이도 와야 한다. 안 오면 내부 모듈 ↔ EdgeTX 경로 문제
4. **0x08, 0x02가 오는가** — 여기서만 안 오면 FC 쪽 텔레메트리 설정 또는 센서 문제. EdgeTX 텔레메트리 페이지에서 센서 발견 여부로 교차 확인

---

## 7. 함정 정리

- **연결 판정은 프레임 유무로 하지 않는다.** 0x14는 링크가 끊겨도 계속 나온다. `up_lq == 0` 이 1초 이상 지속되면 끊김으로 판정하고, 프레임 자체가 300ms 이상 안 오면 USB/핸드셋 문제로 구분한다.
- **갱신 주기는 TLM ratio에 종속.** 1:128이면 GPS/배터리가 초당 1회 미만으로 떨어진다. ELRS Lua에서 TLM ratio를 1:8 정도로 올리면 개선되지만 제어 링크 여유가 줄어든다. 로깅 목적이면 지상 테스트 때만 올리는 편이 안전하다.
- **heading 부호.** FC 구현에 따라 0~35999로 오기도, ±18000(int16)으로 오기도 한다. 실측해서 처리 방식을 확정한다.
- **altitude 오프셋 1000.** GPS 고도는 항상 `-1000` 해야 m가 된다. 빼먹으면 1km 어긋난다.
- **전류계 없는 기체.** current/used_mah가 0으로 고정된다. 하드웨어 부재이지 파싱 오류가 아니다.
- **remaining_pct 신뢰도.** FC의 배터리 프로파일 설정에 의존한다. 셀 수/전압 곡선이 안 맞으면 값이 튄다. 전압을 직접 쓰는 편이 낫다.
- **USB 모드 재선택.** 케이블을 다시 꽂을 때마다 모드 팝업이 뜬다. 매번 Serial을 고르기 귀찮으면 Radio Setup에서 기본 USB 모드를 Serial로 고정한다.
- **CRC 실패 시 1바이트만 밀 것.** 프레임 전체를 버리면 진짜 프레임 시작점을 놓칠 수 있다.
- **이 포트는 수신 전용이다.** 조종 명령(RC_CHANNELS `0x16`)은 안 보이고, DEVICE_PING(`0x28`)·MSP over CRSF(`0x7A`) 요청을 sync `0xC8`/`0xEE`/`0xEA` 로 써도 응답이 없다 (09-12). FC 버전·설정은 FC USB 로 직접 읽어야 한다.
