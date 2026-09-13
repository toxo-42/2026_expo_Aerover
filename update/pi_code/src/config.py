"""설정 한 곳 — 포트 · 경로 · 녹화 품질 · GPIO 핀 · 전송 대상.

환경에 따라 바꾸는 값은 전부 여기 있다. 다른 모듈은 여기서 가져다 쓰고
자기 파일에 따로 적지 않는다.
카메라 해상도·회전 같은 '무엇을 어떻게 찍을지'는 camera/settings.py 에 있다.

경로는 **전부 저장소 루트(`ROOT`) 기준**이다. 하드코딩된 절대경로는 없다 —
`~/drone` 이든 어디든 폴더째 옮겨도 그대로 돈다.
자리마다 다른 값은 환경변수로 덮어쓴다. 코드를 고치지 않아도 된다.

    DRONECAM_DEST_IP=192.168.137.1 python app.py udp
    DRONECAM_FC_SERIAL=/dev/serial0 python app.py stream                     # FC 텔레메트리 중계
    DRONECAM_SOCK=/run/dronecam/control.sock sudo -E python app.py record   # 옛 시스템 소켓 자리
"""
from __future__ import annotations

import os
from pathlib import Path

# 저장소 루트(app.py 가 있는 곳). 파이에서는 ~/drone. `src/config.py` 에서 두 단계 위다.
ROOT = Path(__file__).resolve().parents[1]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# ---- 지상국 링크 (stream) — UDP: MAVLink 로 제어, RTP/JPEG 로 영상 ----

MAVLINK_PORT    = int(_env("DRONECAM_MAVLINK_PORT", "14550"))  # 지상국 HEARTBEAT·명령을 받는 UDP 포트
RTP_PORT        = int(_env("DRONECAM_RTP_PORT", "5004"))       # 지상국이 RTP 를 받는 포트. aerover 와 같아야 한다
RTP_MTU         = 1400                                        # Wi-Fi MTU 1500 안에서 IP 단편화 없이
GCS_TIMEOUT_SEC = 3.0                                         # 지상국 HEARTBEAT 가 이보다 오래 없으면 스트림을 멈춘다
FC_SERIAL       = _env("DRONECAM_FC_SERIAL", "")              # FC 의 MAVLink UART 장치. 비면 텔레메트리 중계 안 함
FC_BAUD         = int(_env("DRONECAM_FC_BAUD", "57600"))      # INAV MAVLink 텔레메트리 기본 보레이트

# ---- 옛 TCP 스트림 (tcp) ----

STREAM_PORT = int(_env("DRONECAM_STREAM_PORT", "5001"))   # aerover 의 TCP_PORT 와 같아야 한다
STREAM_CLIENT_TIMEOUT_SEC = 5.0     # 이 시간 안에 못 보내면 끊긴 것으로 보고 카메라를 놓는다

# ---- 녹화 (record · web 공용) ----

REC_DIR    = ROOT / "rec"           # flight_*.h264 / .pts / .log
BITRATE    = 8_000_000
SYNC_EVERY = 15                     # N프레임마다 fsync (30fps → 약 0.5초)
GAP_MS     = 100                    # 결손으로 판정할 프레임 간격

# ---- 녹화 데몬 (record) ----

RUN_DIR    = ROOT / "run"                                   # 실행 중에만 있는 것들 (소켓)
SOCK_PATH  = _env("DRONECAM_SOCK", str(RUN_DIR / "control.sock"))   # cam 명령이 여기로 접속한다

# GPIO (BCM 번호)
BTN_PIN    = 17                     # 버튼: GPIO17(11번 핀) ── 스위치 ── GND, 내부 풀업 사용
LED_PIN    = 27                     # LED : GPIO27(13번 핀) ── 330Ω ── LED ── GND
BOUNCE_S   = 0.05                   # 채터링 제거 시간

# ---- 웹 미리보기 + 녹화 (web) ----

WEB_HOST   = _env("DRONECAM_WEB_HOST", "0.0.0.0")
WEB_PORT   = int(_env("DRONECAM_WEB_PORT", "8000"))

# ---- 인터벌 UDP 전송 (udp) ----

DEST_IP      = _env("DRONECAM_DEST_IP", "192.168.0.177")   # 지상국(노트북) IP — 망이 바뀌면 환경변수로
DEST_PORT    = int(_env("DRONECAM_DEST_PORT", "9999"))
INTERVAL_SEC = 2.0                  # 촬영 간격
NUM_SHOTS    = None                 # None이면 무한
JPG_QUALITY  = 80                   # 전송량 줄이려 약간 낮춤
