# AeroVer 지상국

재난 구조 드론 지상국 GUI (PySide6). 화면은 세 개다 — **드론 상태 · 3D 매핑 · 요구조자 탐지**.
**아직 GUI 디자인은 수정하지 못했다. 원하는 디자인이 있어서 pyside6 공부하면서 수정중에 있다.**
- gui 콘티는 폴더 내부 gui_conti.pdf를 보면 된다. 색조합은 지금 앰버로 되어있는데, 푸른 계열로 수정하여서 제작할 것이다.

## 실행

[uv](https://docs.astral.sh/uv/) 가 필요하다. 이 폴더(`pyproject.toml` 이 있는 곳)에서:

```bash
uv sync                  # 가상환경 + 의존성 (버전은 uv.lock 고정)
uv run python app.py
uv run pytest            # 66개 — 장비 없이 돈다 (가짜 파이와 UDP 로 이야기하는 것까지)
```

uv 가 없으면 `requirements.txt` 로 만든다 (`pyproject.toml` 과 같은 버전):

```bash
python -m venv .venv
.venv\Scripts\activate            # 윈도우  (맥·리눅스: source .venv/bin/activate)
pip install -r requirements.txt
python app.py
python -m pytest
```

`.venv/` 는 만든 컴퓨터 전용이고 저장소에 넣지 않는다 (맥에서 만든 것은 윈도우에서 안 돈다). 컴퓨터마다 위 명령으로 만든다.

## 앱 밖에서 필요한 것

앱 혼자서는 화면만 뜬다. 값은 아래 상대가 있어야 들어온다. 없어도 앱은 죽지 않고 해당 칸에 오류·미수신을 표시한다.

| 화면 | 상대 | 설정 이름 (환경변수) |
|---|---|---|
| 드론 상태 — 영상 · 수집 | 라즈베리파이 `app.py stream` — UDP: MAVLink 제어(14550) + RTP/JPEG 영상(5004). 옛 TCP 는 `AEROVER_LINK=tcp` | `PI_HOST`, `PI_PORT`, `RTP_PORT`, `LINK_MODE` (`AEROVER_PI_HOST`, `AEROVER_PI_PORT`, `AEROVER_RTP_PORT`, `AEROVER_LINK`) |
| 드론 상태 — 계기판 | 조종기 USB (CRSF 텔레메트리 미러) | `SERIAL_PORT` (`AEROVER_SERIAL`) |
| 3D 매핑 — ③ ODM 제출 | NodeODM | `ODM_HOST`, `ODM_PORT` (`AEROVER_ODM_HOST`, `AEROVER_ODM_PORT`) |
| 요구조자 탐지 | YOLO 가중치 `models/best.pt` (아직 없음) | `DETECT_MODEL_PATH` |

### NodeODM 띄우기

앱은 `ODM_HOST:ODM_PORT` 의 NodeODM HTTP API 에 요청만 보낸다 — Docker 로 띄웠는지 직접 설치했는지는 모른다. 어느 쪽이든 기본 포트는 3000 이다.
`ODM_HOST` 는 OS 가 아니라 **NodeODM 이 도는 컴퓨터**로 정한다. GUI 와 같은 컴퓨터면 `localhost`, 다른 PC 면 그 IP.

| OS | Docker 없이 | Docker | GPU |
|---|---|---|---|
| macOS | 공식 방법 없음 | `docker run -p 3000:3000 opendronemap/nodeodm` | 불가 (CUDA 전용) |
| Windows | [ODM](https://github.com/OpenDroneMap/ODM/releases) 설치 → [NodeODM](https://github.com/OpenDroneMap/NodeODM/releases) `nodeodm-windows-x64.zip` 풀고 `nodeodm.exe --odm_path <ODM 폴더>` | 위와 같음 | NVIDIA 만 — `docker run --gpus all -p 3000:3000 opendronemap/nodeodm:gpu` |
| Ubuntu | ODM 설치 후 NodeODM 에서 `node index.js --odm_path <ODM 경로>` | 위와 같음 | 위와 같음 |

- 한 번 `docker run` 으로 만든 컨테이너는 다음부터 `docker start <이름>` 으로 켠다 (이 맥은 `nodeodm_test`). **컨테이너를 새로 만들면 작업 기록이 사라진다** — 회차 폴더의 `odm_uuid.txt` 를 지우고 다시 제출해야 한다.
- 떴는지 확인: `http://<ODM_HOST>:3000/info` 에 `"engine":"odm"` 이 보이면 된다.
#### 참고사항
GPU가 있는 컴퓨터에선 안돌려봐서 테스트 필요함

## 기체 · 통신

세 줄기가 따로 흐른다. **우리 코드는 받는 쪽 둘(`core/telemetry/`, `core/link.py`)과 파이 송신(`../pi_code/`)뿐**이고, 조종 명령과 비행 컨트롤러(FC)는 전부 외부 펌웨어다.

```
조종 명령   스틱 → EdgeTX → 내장 ELRS 모듈 ─2.4GHz→ ELRS 수신기 ─CRSF(UART)→ FC(INAV)
텔레메트리  FC(INAV) ─CRSF(UART)→ ELRS 수신기 ─2.4GHz→ 내장 ELRS 모듈 → EdgeTX ─USB 미러→ core/telemetry
영상·제어   파이 app.py stream ←MAVLink/UDP 14550→ core/link.py (RtpLinkWorker) ← RTP/JPEG UDP 5004
FC 중계     FC(INAV) ─MAVLink(UART)→ 파이 ─UDP 중계→ core/telemetry/mavlink.py   (배선 전 — 준비만 됨)
```

| 장비 | 돌아가는 것 | 우리가 다루는 것 |
|---|---|---|
| FC — SpeedyBee F405 V4 | INAV 9.1.0 | 설정만 — 백업 `inav_diff_all_20260912.txt` (**이 저장소에는 아직 없다**) |
| 조종기 — Radiomaster Pocket | EdgeTX `RM Factory (1fdb58ba)` + 내장 ExpressLRS 3.6.4 | 모델 설정, USB 설정 |
| 수신기 — BETAFPV Nano 2.4G RX | ExpressLRS 3.2.0 | 바인드 |
| 라즈베리파이 | `app.py stream` | `../pi_code/` |

### FC — SpeedyBee F405 V4 + INAV

- **저장소에 FC 코드는 없다.** 순정 INAV 를 Configurator 로 설정해서 쓴다. 문서로 남길 건 코드가 아니라 **버전과 설정 백업**이다.
- **버전: `INAV/SPEEDYBEEF405V4 9.1.0 Jul 8 2026`**, MSP API 2.5. 설정 원문은 `inav_diff_all_20260912.txt` (2026-09-12). Configurator CLI 탭에 붙여넣으면 복원된다.
- 버전·설정은 **FC 를 컴퓨터에 USB-C 로 꽂아야** 읽힌다 (맥에서는 `/dev/tty.usbmodem*` 중 USB 이름이 `INAV SpeedyBeeF405V4` 인 것). 조종기 포트로는 못 읽는다(아래 "조종 명령"). CLI 에서 `exit` 하면 **저장 없이 재부팅**한다.

| 설정 | 값 | 뜻 |
|---|---|---|
| UART6 | `serial 5 64` | 수신기(RX_SERIAL), `serialrx_provider = CRSF` |
| UART3 | `serial 2 2` | GPS |
| UART2 | `serial 1 0` | 기능 없음 |
| MSP | USB 만 (기본값) | **MSP 가 켜진 UART 가 없다** — 파이를 붙일 자리가 아직 없다 (성한이가 납땜해서 추가할 예정)|
| feature | GPS · TELEMETRY · VBAT · CURRENT_METER · BLACKBOX · AIRMODE · OSD · PWM_OUTPUT_ENABLE · TX_PROF_SEL | MSP 비트마스크 `0x30480C86` 해석 |
| 센서 | 가속도 ICM42605 · 기압 SPL06 · 지자기 NONE | |
| 모터 | DSHOT300, 쿼드 X (`mmix` 4개), `throttle_scale 0.7` | |
| ARM | `aux 0 0 0 1800 2025` | AUX1(CH5) 이 1800~2025 일 때 arming. 비행 모드 스위치는 없다 |
| 페일세이프 | `failsafe_procedure = DROP` | **링크가 끊기면 모터를 끈다** (RTH 아님) |

- 텔레메트리에 무엇이 오는지는 FC 설정이 정한다. TELEMETRY feature 가 켜져 있어 수신기 UART(CRSF)로 텔레메트리가 되돌아간다.
- **GPS feature 가 켜져 있으면 GPS 모듈이 없어도 GPS 프레임이 0 으로 오고, 비행 모드가 `WAIT` 로 뜬다** (2026-09-12, 모듈 분리 상태 실측). 파싱 오류가 아니다.

### 조종 명령 — 조종기 → FC

- **저장소에 코드는 없고, 지상국은 명령을 보내지 않는다.** EdgeTX 가 스틱을 채널로 믹싱해 내장 ELRS 모듈로 넘기고, 전파 → 수신기 → FC UART6 로 간다. 채널이 어떤 동작에 붙는지는 INAV Modes 탭 설정(`aux` 줄)이고, 지금은 ARM 하나뿐이다 (위 표).
- 조종기 설정 (2026-09-12, 조종기 화면에서 확인):

  | 항목 | 값 | 확인 위치 |
  |---|---|---|
  | EdgeTX | `RM Factory (1fdb58ba)` — 라디오마스터 출하 빌드, 괄호는 커밋 해시 | `SYS` → Version |
  | ELRS 모듈 | `3.6.4 ISM2G4 b61c9e` | `SYS` → Tools → ExpressLRS 맨 아래 |
  | Packet Rate | 333Hz Full | 〃 |
  | Telem Ratio | 1:64 (256bps) — GPS·배터리가 초당 0.5회쯤 오는 이유 (실측 10초에 5개) | 〃 |
  | Switch Mode | 8ch | 〃 |
  | Link Mode | Normal | 〃 |
  | 수신기 | BFPV Nano 2G4RX, ELRS 3.2.0 — 모듈(3.6.4)과 메이저 버전 3 이 같아 호환된다 | 〃 → Other Devices |

#### 참고사항
`crsf-telemetry-sniffing.md` (이 폴더) 안에 성한이가 정리해준 컨트롤러 데이터 전송 방식 정리본 같이 참고용으로 넣어둠

### 텔레메트리 — FC → 지상국

- 파싱은 `src/core/telemetry/crsf.py`. 프레임 형식·실측값·함정은 `crsf-telemetry-sniffing.md`.
- 조종기 설정: **USB Mode = Serial**, **USB VCP = Telemetry Mirror**. 기본값이면 포트는 열리는데 바이트가 0 이다.
- 미러로 오는 프레임의 sync 는 `0xEA` 다 (FC 직결이면 `0xC8`). 받는 것: LINK_STATS · BATTERY · GPS · ATTITUDE · BARO_ALT · VARIO · FLIGHT_MODE.
- 시리얼 포트는 한 프로세스만 연다. GUI 가 켜져 있으면 `python -m src.core.telemetry` 나 다른 스크립트가 못 붙는다.
- 파이가 FC 의 MAVLink 를 중계하면(위 "영상 · 제어") 같은 계기판 칸에 들어온다. 둘 다 오면 나중에 온 값이 남는다.
- 포트 이름은 OS 마다 다르다. 기본값은 윈도우 `COM5`, 맥은 꽂혀 있는 `/dev/tty.usbmodem*`, 리눅스 `/dev/ttyACM0` 이고, 다르면 `AEROVER_SERIAL` 로 준다.

### 영상 · 제어 — 파이 ↔ 지상국 (UDP)

- 지상국이 [연결] 을 누르면 `RtpLinkWorker` 가 파이의 MAVLink 포트(14550)로 HEARTBEAT 와 `VIDEO_START_STREAMING` 을 보낸다. 파이 HEARTBEAT 가 오면 "연결됨", 파이는 지상국 주소의 5004 로 RTP/JPEG 를 쏜다. [해제] 는 `VIDEO_STOP_STREAMING`. 지상국 HEARTBEAT 가 3초 끊기면 파이가 스스로 멈춘다.
- RTP/JPEG(RFC 2435)는 JPEG 헤더를 벗기고 스캔 데이터만 보낸다. 받는 쪽이 표준 허프만 테이블로 되살리므로 **픽셀은 원본과 같다.** 패킷이 빠진 프레임은 버린다 (재전송 없음). 코덱은 `src/core/rtpjpeg.py` — 파이와 같은 파일이다.
- 5초 안에 파이 HEARTBEAT 가 없으면 "파이 응답 없음", 파이가 카메라를 못 열면 "파이가 스트리밍을 거부했다 — …" 로 뜬다.
- 옛 TCP 방식: 파이 `app.py tcp` + 지상국 `AEROVER_LINK=tcp` (포트 5001). 화면 코드는 두 방식이 같다.

### 파이 ↔ FC 직접 연결 (MSP) — 미구현 (성한이가 추가예정임)

지금 파이는 FC 와 통신하지 않는다 — `pi_code/` 에 MSP·시리얼 코드가 없고, FC 에도 MSP 를 켠 UART 가 없다 (USB 만). 파이가 FC 값을 직접 읽거나 명령을 내려야 해지면 이 방식을 쓴다.

- INAV 에서 컴패니언 컴퓨터가 **명령까지** 주고받는 공식 방법은 MSP 다. MAVLink 는 INAV 문서(`docs/Telemetry.md`)에 "transmit-only" — 텔레메트리만 나간다.
- **텔레메트리만 필요하면 MAVLink 중계가 이미 준비돼 있다.** FC 의 빈 UART 에 MAVLink 텔레메트리를 켜고 파이에 배선한 뒤 `DRONECAM_FC_SERIAL=<장치>` 로 `app.py stream` 을 띄우면, 파이가 메시지를 UDP 로 중계하고 계기판(`core/telemetry/mavlink.py`)이 GPS · 자세 · 배터리 · 기압고도 · 상승률을 CRSF 와 같은 칸에 넣는다.
- 연결: 파이 시리얼 포트를 켜고, FC 의 빈 UART 에 MSP 를 설정해 배선한다.
- **MSP V2 기준으로 한다.** V1 은 메시지 ID·페이로드가 255 로 막히고 체크섬이 XOR 이다. V2 는 16비트 ID 와 CRC8(DVB-S2, poly `0xD5` — CRSF 와 같은 CRC)을 쓴다.
- 요청-응답 구조다. 요청하는 파이가 Master, 응답하는 FC 가 Slave.

## 설정

환경마다 바꾸는 값은 **전부 `src/config.py` 한 곳**에 있다. 다른 파일에 주소·포트·경로를 새로 적지 않는다.

- **경로는 전부 저장소 루트 기준이다.** `sessions/`, `3D_model/`, `models/best.pt`, `cameras_imx477_6mm.json` 은 `app.py` 옆에서 찾는다. 절대경로는 코드 어디에도 없다.
- 회차 산출물 폴더(`3D_model/<회차>/`)가 어느 촬영본에서 나왔는지는 `source.txt` 에 **루트 기준 상대경로**로 적는다 (`sessions/20260907_205517` 처럼). 다른 컴퓨터에서 적힌 옛 기록은 폴더명으로 맞춘다 — 그래서 맥에서 만든 `3D_model/` 을 윈도우에서 그대로 쓴다.
- 장비 주소·포트는 환경변수 `AEROVER_PI_HOST` · `AEROVER_PI_PORT` · `AEROVER_LINK` · `AEROVER_MAVLINK_PORT` · `AEROVER_RTP_PORT` · `AEROVER_SERIAL` · `AEROVER_ODM_HOST` · `AEROVER_ODM_PORT` 로 덮어쓴다. `RTP_PORT` 와 `MAVLINK_PORT` 는 파이 설정과 같아야 한다.

## 구조

```
aerover/
├── app.py                   실행 진입점
├── pyproject.toml           의존성 · 패키지 정의 · pytest 설정
├── uv.lock                  의존성 버전 고정
├── cameras_imx477_6mm.json  파이캠 내부 파라미터 (EXIF 없는 수집본을 ODM 에 넣을 때 쓴다)
├── crsf-telemetry-sniffing.md  CRSF 프레임 형식 · 실측
├── sessions/                드론 수집본 — 수집 회차마다 폴더
├── 3D_model/                ODM 산출물 — 입력 촬영본마다 폴더 (source.txt 가 원본을 가리킨다)
├── tests/                   장비 없이 도는 테스트 (uv run pytest)
└── src/
    ├── config.py            설정 한 곳 — 경로는 ROOT 기준, 장비는 환경변수
    ├── main_window.py       앱 셸 — 사이드바 · 페이지, 영상 링크와 텔레메트리 수신의 소유자
    ├── core/                로직. 화면(pages · ui)을 import 하지 않는다
    │   ├── rtpjpeg.py       RTP/JPEG 코덱 (RFC 2435) — 파이와 같은 파일
    │   ├── gcs.py           지상국 MAVLink 끝점 — HEARTBEAT · 스트리밍 명령 · ACK
    │   ├── framing.py       옛 TCP 전송 규약 (길이 4바이트 + JPEG) · FrameReader
    │   ├── link.py          파이 영상 수신 — RtpLinkWorker(UDP, 기본) · LinkWorker(TCP) · LinkHub(유일한 소유자)
    │   ├── telemetry/       조종기 CRSF + 파이가 중계하는 MAVLink
    │   │   ├── crsf.py      CRSF 프레임 파싱 (순수 함수). 종류 추가는 FRAMES 표에 한 줄
    │   │   ├── mavlink.py   MAVLink 메시지 → 같은 상태 키. 추가는 HANDLERS 표에 한 줄
    │   │   ├── state.py     공유 상태 보관함 · age · last_error
    │   │   ├── status.py    연결 판정 LinkJudge (NO_DATA / LOST / WEAK / OK)
    │   │   ├── home.py      이륙지점 HomePoint · 거리 · 방위
    │   │   └── receiver.py  SerialReader 스레드 · 앱이 공유하는 기본 인스턴스 (start · get_telemetry)
    │   ├── capture.py       수집 회차 저장
    │   ├── imgcheck.py      ODM 투입 전 촬영본 검사 — ImageInspector · RULES · check_folder
    │   ├── subsample.py     연사본 선별
    │   ├── odm.py           NodeODM — OdmJob(순수 순서) · OdmWorker(Qt 어댑터)
    │   ├── cropper.py       메시 배경 제거 — 바닥 평면 · CropBox · 텍스처 축소
    │   ├── workspace.py     회차 산출물 폴더 찾기·만들기 (ModelStore, source.txt 상대경로)
    │   └── detect.py        YOLO 탐지 자리 — best.pt 가 오면 detect() 만 채운다. DetectWorker 는 탐지기를 주입받는다
    ├── gl/                  3D 뷰포트 (OpenGL)
    ├── ui/                  공용 위젯 · 색
    └── pages/               화면
        ├── status.py        드론 상태
        ├── mapping/         3D 매핑 — page · steps · workers
        └── detect.py        요구조자 탐지
```

의존 방향은 `pages → core · gl · ui` 한쪽이다. 새 로직은 `core/` 에 두고 화면은 그걸 부르기만 한다.
`core/` 는 Qt 를 모르는 순수 로직(파싱·판정·작업 순서)과 그것을 스레드·시그널로 감싸는 어댑터(`*Worker`)로 나뉜다. 확장은 표에 한 줄 더하는 것으로 끝난다 — 새 프레임은 `crsf.FRAMES`, 새 검사 규칙은 `imgcheck.RULES`.

## GUI 없이 실행

```bash
uv run python -m src.core.telemetry          # 조종기 수신값을 0.5초마다 출력
uv run python -m src.core.imgcheck [폴더]     # 촬영본 검사 결과 출력
uv run python -m src.pages.mapping --demo    # 매핑 페이지만 띄운다
uv run pytest                                # 테스트
```

## 남은 일 — GUI 쪽 (2026-09-13 점검, 아직 손대지 않음)

- `pages/mapping/page.py` `_run_odm`: cameras.json 을 줄지 말지를 메모리의 검사 결과로만 정한다. 앱을 다시 켠 뒤 디스크의 선별본으로 바로 제출하면 EXIF 없는 수집본에도 cameras.json 을 안 보낸다. 제출할 이미지에서 `imgcheck.read_exif` 로 직접 판정할 것.
- `pages/mapping/page.py` `_on_odm_failed`: 노드가 uuid 를 모르면(`NodeResponseError`) `odm_uuid.txt` 를 안 지워 그 회차를 재제출할 수 없다. `TaskFailedError` 와 같이 지울 것. `OdmWorker.cancelled` 시그널도 아직 페이지가 받지 않는다 — 받아서 uuid 파일을 지우면 취소 뒤 바로 재제출된다.
- `ui/video.py`: QLabel 에 pixmap 을 넣으면 창을 그 크기 아래로 못 줄인다. `label.setMinimumSize(1, 1)`.
- `pages/detect.py`: 3D 맵 모드 뷰포트에 모델을 올리는 경로가 없다 (3차 보류 중이면 그대로).
- `ui/palette.py`: 버튼 hover/pressed 가 앰버(#D97706/#B45309) 그대로다. 파란 계열로 바꿀 때 같이.
