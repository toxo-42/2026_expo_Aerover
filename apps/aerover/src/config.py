"""설정 한 곳 — 장비 주소 · 포트 · 데이터 경로.

환경에 따라 바꾸는 값은 전부 여기 있다. 다른 모듈은 여기서 가져다 쓰고
자기 파일에 따로 적지 않는다.

경로는 **전부 저장소 루트(`ROOT`) 기준으로 계산한다.** 하드코딩된 절대경로는
없다 — 폴더를 통째로 옮기거나 다른 OS 로 가져가도 그대로 돈다.

장비 주소·포트처럼 자리마다 다른 값은 환경변수로 덮어쓴다. 코드를 고치지 않아도 된다.

    AEROVER_PI_HOST=192.168.0.10 AEROVER_SERIAL=COM7 uv run python app.py
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

# 저장소 루트(app.py 가 있는 곳). `src/config.py` 에서 두 단계 위다.
ROOT = Path(__file__).resolve().parents[1]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


# ---- 데이터 (코드가 아니라 src 밖에 둔다) ----

# 드론 수집본. 상태 페이지가 쌓고 매핑 페이지가 읽는다 — 두 곳이 같은 곳을 봐야 한다.
SESSIONS_DIR = ROOT / "sessions"

# 3D 산출물은 **입력 폴더가 어디든 늘 여기 모인다.** 나중에 찾기 쉽게.
MODELS_DIR = ROOT / "3D_model"

# 사이드바 아이콘 (SVG).
ICON_DIR = ROOT / "icon"

# 맵에 그리는 드론 메시 — `tools/drone_mesh.py` 가 원본 STL 에서 줄여 만든다. 없으면 사각뿔로 그린다.
DRONE_MESH_FILE = ROOT / "models" / "drone_mesh.npz"

# YOLO 가중치. `models/` 에서 찾는다 — 팀 학습본(best.pt)이 있으면 그것을 쓰고,
# 없으면 사전학습 yolo11n.pt (person/vehicle 로 통합해 쓴다, `core/detect.py`).
# 둘 다 없으면 best.pt 이름을 그대로 들고 있는다 — 화면이 그 이름으로 "없음"을 안내한다.
WEIGHTS_DIR = ROOT / "models"
DETECT_MODEL_PATH = next(
    (WEIGHTS_DIR / n for n in ("best.pt", "yolo11n.pt") if (WEIGHTS_DIR / n).is_file()),
    WEIGHTS_DIR / "best.pt",
)

# 추론 입력 크기. 팀 학습본(r1011)은 1280 으로 학습했다 — 640 으로 물으면 요구조자 핀이 더 갈라진다
# (회차 20261004_130739: 1280 → 24, 640 → 26, 실제 23~25). CPU 에서 1280 은 프레임당 ~110ms. 느리면 960 · 640.
DETECT_IMGSZ = int(_env("AEROVER_DETECT_IMGSZ", "1280"))

# 파이캠(IMX477 + 6mm 광각)의 내부 파라미터. 수집본에는 EXIF 가 없어 ODM 이
# 초점거리를 0.85 로 때려맞춘다. 실제 값은 1.111 (= 6.61mm).
# 이 값은 회차 aab831fa 의 **번들조정 수렴값**이다 — 체스보드 캘리브레이션이 아니다.
# **EXIF 가 있는 입력(폰 사진 등)에는 주지 않는다** — 그건 ODM 이 스스로 계산한다.
CAMERAS_FILE = ROOT / "cameras_imx477_6mm.json"

# 시연 좌표계 W (apps/aruco). 자세 추정 · 역투영 코드와 입력 파일(카메라 K · 마커 배치표)이
# 거기 있다 — aerover 는 `core/wframe.py` 한 곳에서만 가져다 쓴다.
ARUCO_DIR = ROOT.parent / "aruco"
TO_W_FILE = "to_W.json"  # 회차 폴더 안. ODM → W 4x4 (aruco/align_model.py 가 만든다)

# 탐지 화면의 자세 추정에 쓰는 카메라 K · 왜곡 (1280×960). 기본은 파이캠 체스보드 캘리브레이션.
# 장비 없이 사진을 재생할 때(`tools/fake_pi.py`)는 그 사진을 찍은 카메라의 값으로 바꿔 끼운다 —
# 폰 사진을 파이캠 값으로 풀면 재투영 오차가 14px 까지 난다 (폰 값이면 2px).
POSE_CAMERA_FILE = Path(_env("AEROVER_CAMERA", str(ARUCO_DIR / "camera_1280x960.json")))

# 마커 배치표. 기본은 지금 판의 것. **마커를 다시 붙이기 전에 찍은 사진을 재생할 때는** 그때의 배치표로
# 바꿔 끼운다 — 틀린 배치표로 풀면 자세가 틀어져 같은 객체가 5~10cm 씩 흩어지고 핀이 갈라진다
# (2026-10-11 재접착: 그 이전 회차는 `markers_W_60mm_before_glue.json`).
POSE_LAYOUT_FILE = Path(_env("AEROVER_LAYOUT", str(ARUCO_DIR / "markers_W.json")))

# ---- 장비 · 파이 링크 ----

# 라즈베리파이. 망이 바뀌면 파이에서 `hostname -I` 로 다시 확인한다.
PI_HOST = _env("AEROVER_PI_HOST", "192.168.0.4")

# 영상 링크 방식.  rtp = UDP — MAVLink 로 제어하고 RTP/JPEG 로 영상을 받는다 (기본)
#                 tcp = 옛 방식 — 길이 4바이트 + JPEG 를 TCP 로 받는다 (파이 `app.py tcp`)
LINK_MODE = _env("AEROVER_LINK", "rtp")
MAVLINK_PORT = int(
    _env("AEROVER_MAVLINK_PORT", "14550")
)  # 파이 MAVLink 노드가 듣는 UDP 포트
RTP_PORT = int(
    _env("AEROVER_RTP_PORT", "5004")
)  # 지상국이 RTP 영상을 받는 UDP 포트. 파이 설정과 같아야 한다
TCP_PORT = 5001  # 옛 TCP 스트림 포트
# 화면의 "호스트:포트" 칸 기본값 — rtp 면 파이의 MAVLink 포트, tcp 면 스트림 포트
PI_PORT = int(
    _env("AEROVER_PI_PORT", str(MAVLINK_PORT if LINK_MODE == "rtp" else TCP_PORT))
)


def _default_serial_port() -> str:
    """조종기 USB(VCP) 포트의 OS 별 기본값.

    맥은 장치명이 케이블·포트마다 달라 지금 꽂혀 있는 것을 찾는다. 둘 이상이면
    (FC 도 같이 꽂은 경우) `AEROVER_SERIAL` 로 지정한다.
    """
    if sys.platform == "win32":
        return "COM5"  # 장치 관리자 → 포트 에서 확인
    if sys.platform == "darwin":
        found = sorted(glob.glob("/dev/tty.usbmodem*"))
        return found[0] if found else "/dev/tty.usbmodem0"
    return "/dev/ttyACM0"  # 리눅스. dialout 그룹 필요


SERIAL_PORT = _env("AEROVER_SERIAL", _default_serial_port())
SERIAL_BAUD = 115200

# NodeODM. GUI 와 같은 컴퓨터면 localhost, 다른 PC 면 그 IP.
ODM_HOST = _env("AEROVER_ODM_HOST", "localhost")
ODM_PORT = int(_env("AEROVER_ODM_PORT", "3000"))

# ---- 비행 ----

TARGET_AGL_M = 15  # 목표 고도 (m)
