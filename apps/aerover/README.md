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
| 드론 상태 — 계기판 | 조종기 USB (CRSF 텔레메트리 미러) · 파이가 중계하는 FC MAVLink (선택) | `SERIAL_PORT` (`AEROVER_SERIAL`) |
| 3D 매핑 — ③ ODM 제출 | NodeODM | `ODM_HOST`, `ODM_PORT` (`AEROVER_ODM_HOST`, `AEROVER_ODM_PORT`) |
| 요구조자 탐지 | YOLO 가중치 `models/` — 팀 학습본 `best.pt` 가 있으면 그것, 없으면 사전학습 `yolo11n.pt` | `DETECT_MODEL_PATH`, `DETECT_IMGSZ` (`AEROVER_DETECT_IMGSZ`) |

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
FC 중계     FC(INAV) ─MAVLink(UART)→ 파이 ─UDP 중계→ core/telemetry/mavlink.py   (FC→파이 수신 확인 09-15 · 지상국 중계는 아직 안 켬)
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
| MSP | USB 만 (기본값, 09-12 백업 기준) | **MSP 가 켜진 UART 가 없다** — 파이 ↔ FC MSP 는 미구현 (성한이가 추가예정) |
| 파이 연결 UART | MAVLink 텔레메트리 57600 | 2026-09-15 Configurator 에서 켬 — **몇 번 UART 인지와 `diff all` 백업은 아직 안 적었다** |
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

MSP 코드는 없다 — `pi_code/` 의 FC 연결은 **MAVLink 텔레메트리 읽기**(`control/fc_bridge.py`)뿐이다. 파이가 FC 에 명령을 내리거나 설정값을 읽어야 해지면 이 방식을 쓴다.

- INAV 에서 컴패니언 컴퓨터가 **명령까지** 주고받는 공식 방법은 MSP 다. MAVLink 는 INAV 문서(`docs/Telemetry.md`)에 "transmit-only" — 텔레메트리만 나간다.
- **텔레메트리만 필요하면 MAVLink 중계로 된다.** FC 의 빈 UART 에 MAVLink 텔레메트리를 켜고 파이에 배선한 뒤 `DRONECAM_FC_SERIAL=<장치>` 로 `app.py stream` 을 띄우면, 파이가 메시지를 UDP 로 중계하고 계기판(`core/telemetry/mavlink.py`)이 GPS · 자세 · 배터리 · 기압고도 · 상승률을 CRSF 와 같은 칸에 넣는다.
- **2026-09-15 FC → 파이 수신 확인.** FC T → 파이 GPIO15(10번 핀) · GND, 파이 시리얼 콘솔 끔(`raspi-config` → Serial Port: login shell No / hardware Yes), `/dev/serial0` 57600 에서 HEARTBEAT · ATTITUDE · SYS_STATUS · BATTERY_STATUS · VFR_HUD · SCALED_PRESSURE 수신.
  - INAV 기본 전송률이 1~2Hz 다. 계기판이 느리면 CLI 에서 `mavlink_extra1_rate`(ATTITUDE) 등을 올린다.
  - GPS_RAW_INT 는 오지 않는다. **원인은 GPS 배선 없음** (2026-09-20 확인). 프로토콜 문제가 아니다.
  - 지상국까지 중계 켬 (2026-09-20) — 아래 "2026/09/20 수정사항".
- 연결: 파이 시리얼 포트를 켜고, FC 의 빈 UART 에 MSP 를 설정해 배선한다. **MSP 는 요청-응답이라 선이 하나 더 필요하다** — FC T → 파이 GPIO15, FC R ← 파이 GPIO14(8번 핀), GND.
- **MSP V2 기준으로 한다.** V1 은 메시지 ID·페이로드가 255 로 막히고 체크섬이 XOR 이다. V2 는 16비트 ID 와 CRC8(DVB-S2, poly `0xD5` — CRSF 와 같은 CRC)을 쓴다.
- 요청-응답 구조다. 요청하는 파이가 Master, 응답하는 FC 가 Slave.

## 요구조자 탐지 — 실시간

**카메라를 따로 열지 않는다.** 노트북 웹캠이 아니라 파이 영상을 그대로 쓴다:

```
파이 app.py stream ─RTP/JPEG UDP 5004→ core/link.py ─QImage→ LinkHub
   → pages/detect.py [실시간] → DetectWorker.submit → core/detect.py YoloDetector → 화면 박스
```

- **연결은 드론 상태 페이지에서 한 번만** 한다. 파이는 클라이언트를 하나만 받아서, 탐지 페이지는 같은 링크를 구독만 한다.
- 순서: 드론 상태에서 [연결] → 요구조자 탐지 → [실시간] → [탐지 시작]. 링크가 없으면 버튼이 잠기고 이유가 적힌다.
- 가중치는 `models/` 에서 찾는다. `best.pt` 가 있으면 그것을, 없으면 `yolo11n.pt` 를 쓴다.
  **사전학습(COCO) 모델이면** 클래스를 `person` · `vehicle` 로 통합하고 나머지는 버린다 (`core/detect.py` 의 `COCO_KEEP` — `camtest.py` 와 같은 표).
  **팀 학습본이면** 클래스 이름을 그대로 쓴다. 판정은 모델의 클래스 이름을 보고 자동이다 (`is_coco`).
- 모델은 **탐지 시작을 누른 순간 워커 스레드에서 연다.** ultralytics·torch import 에 몇 초가 걸려서, 그동안 화면에 "모델 여는 중…" 이 뜬다. 앱이 뜰 때 열지 않는다.
- 추론 중에 들어온 프레임은 **버린다.** 큐에 쌓으면 추론이 링크보다 느릴 때 지연이 무한히 늘어난다 — 실시간 화면에 필요한 것은 지금 것이다.
- 슬라이더의 신뢰도는 **추론 뒤에** 건다. 돌아가는 중에 움직여도 다시 추론하지 않는다. 모델에는 항상 슬라이더 최소값(0.05)으로 묻는다.
- 잘 안 잡히면 `AEROVER_DETECT_IMGSZ=960`, 느리면 `480`.

## 모형 학습 — best.pt 만들기

사전학습 모델(`yolo11n.pt`)은 **진짜 사람은 잘 잡지만 모형은 거의 못 잡는다.** 시연은
모형으로 하므로 우리 수집본으로 다시 가르쳐야 한다. 도구는 `tools/` 에 있다 (앱이 아니라
사람이 쓰는 CLI 라 `src/` 밖에 둔다).

```
① 수집    앱 [드론 상태] → 수집 제어                    →  sessions/<회차>/*.jpg
② 사전라벨 uv run python -m tools.autolabel              →  같은 폴더에 *.txt (초안)
③ 고르기  uv run python -m tools.pick <회차> --count 10  →  label/<회차>_r1/
④ 확인    uv run python -m tools.review label/<회차>_r1  →  review/ 에 상자를 그린 사본
⑤ 보정    uv run python -m tools.label_gui label/<회차>_r1  ←  **여기가 진짜 일이다**
⑥ 묶기    uv run python -m tools.make_dataset label/<회차>_r1  →  dataset/
⑦ 학습    uv run python -m tools.train --install         →  models/best.pt
⑧ 투입    앱을 다시 켠다                                  →  config 가 best.pt 를 먼저 집는다
```

**한 바퀴에 50장을 다 손보지 않는다.** ③이 묶음마다 가장 선명한 한 장씩만 뽑아주므로
10장으로 한 바퀴를 돌리고, 나온 `best.pt` 로 다음 바퀴를 사전라벨한다 (아래 "반복이 정상이다").

명령은 전부 `apps/aerover/` 에서 돈다. 경로는 이 폴더(`config.ROOT`) 기준이라 저장소를
옮겨도 같은 자리를 가리킨다. 위 ①~⑧ 이 만드는 것:

```
apps/aerover/
├── sessions/<회차>/              수집본 원본 — 라벨 작업이 여기를 덮지 않는다
│   ├── 000.jpg · 000.txt        autolabel 이 이미지 옆에 같은 이름으로 깐다
│   └── classes.txt              person · vehicle (순서가 곧 클래스 번호)
├── label/<회차>_r1/         ←   손으로 고치는 곳. pick 이 뽑아서 복사해 둔다
├── review/<회차>_r1/            상자를 그린 확인용 사본 (언제든 다시 만든다)
├── dataset/                     make_dataset 이 만든다
│   ├── data.yaml                학습이 읽는 설명서 (경로 · 클래스)
│   ├── images/train · val
│   └── labels/train · val       이미지와 같은 이름의 .txt
├── runs/<name>/weights/best.pt  train 의 결과
└── models/best.pt           ←   앱이 읽는 자리 (없으면 models/yolo11n.pt 로 돈다)
```

`dataset/` · `runs/` · `review/` · `*.pt` 는 `.gitignore` 가 뺀다 — 전부 다시 만들 수 있다.
**남겨야 하는 원본은 `sessions/` 와 `label/` 의 `.txt` 뿐이다** (이미지는 `*.jpg` 규칙으로 빠진다).

### 데이터를 어떻게 모으는가 (①)

- **반드시 파이 카메라로.** 웹캠으로 모으면 고도·내려다보는 각도·6mm 광각 왜곡이 전부 달라 효과가 없다.
- 고도·각도·조명·모형 배치를 바꿔가며 **200~400장**. 회차를 나눠 찍어도 ④가 합쳐준다.
- **진짜 사람이 찍힌 장을 반드시 섞는다.** 모형만 학습시키면 원래 알던 사람을 잊는다
  (catastrophic forgetting). 모형과 사람이 한 화면에 같이 있으면 제일 좋다.
- 모형도 라벨은 `person` 이다. "모형사람" 이라는 클래스를 새로 만들지 않는다 — 모형은
  요구조자를 대신하는 것이고, 클래스를 늘리면 화면·로그·탐지 코드가 전부 따라 복잡해진다.

### 사전라벨은 초안일 뿐이다 (②③)

`tools/autolabel.py` 는 화면을 **4x3 으로 겹치게 쪼개** 조각마다 추론한다. 모형이 20~30픽셀이라
통째로 넣으면 YOLO 가 거의 못 보기 때문이다 (실측: 통째로 8개 → 타일 20개).
흐린 장은 자동으로 건너뛴다 (`--min-blur`, 기본 80).

**2026-09-16 실측으로 확인한 두 가지:**

- **큰 모델은 소용없다.** `yolo11x`(114MB)가 `yolo11n` 과 탐지 수가 똑같았고 장당 4초 → 28초로 느려지기만 했다.
- **잔해에 누운 모형은 자동으로 안 잡힌다.** 사전학습 모델이 아는 `person` 은 대부분 서 있는 사람이다.
  서 있는 사람·구조대원·차량은 잘 잡는데 **쓰러진 모형만 골라서 못 본다** — 하필 요구조자 탐지의 핵심이다.
  **이건 손으로 그려 넣어야 하고, 그게 이 작업의 전부다.**

```bash
uv run python -m tools.autolabel                      # 가장 최근 회차
uv run python -m tools.autolabel sessions/20260916_190000 --conf 0.03 --imgsz 1280
```

이미 `.txt` 가 있으면 건드리지 않는다 — 고쳐놓은 라벨을 다시 돌렸다가 날리지 않기 위해서다
(`--overwrite` 를 줘야 덮어쓴다).

라벨은 **이 저장소의 도구**로 단다. 따로 설치할 것이 없다:

```bash
uv run python -m tools.label_gui label/20260916_185141_r1
```

| 조작 | |
|---|---|
| 좌클릭 드래그 | 상자 그리기 (현재 클래스로) |
| 상자 안 클릭 | 선택 — 겹쳐 있으면 **가장 작은** 상자가 잡힌다 (큰 오탐 위의 사람 상자를 집기 위해) |
| `Delete` | 선택한 상자 지우기 |
| `1` / `2` | 클래스 바꾸기. 선택 중이면 그 상자의 클래스도 같이 바뀐다 |
| 휠 | **확대 · 축소** — 커서 밑 지점이 제자리에 있도록 확대한다 |
| 가운데 버튼 드래그 | 이동 |
| `F` | 화면에 맞추기 |
| `A` / `D` | 이전 · 다음 장 (**넘어갈 때 자동 저장**) |
| `Ctrl+Z` / `Ctrl+S` | 되돌리기 / 저장 |

**확대가 이 도구의 존재 이유다.** 모형이 20~30픽셀이라 화면에 맞춰 띄우면 점으로 보여
상자를 칠 수가 없다. 바깥 도구를 쓰지 않은 것도 그래서다 — X-AnyLabeling 은 1GB 가 넘고
labelImg 는 PyQt5 라 이 환경의 PySide6 와 부딪친다.

좌표 변환·선택·저장 규칙은 Qt 를 모르는 순수 함수로 `tools/labelio.py` 에 있다 (테스트가 있다).

**`classes.txt` 의 순서를 바꾸지 마라.** 줄 번호가 곧 클래스 번호라, 바꾸면 이미 그린 라벨이 전부 다른 것을 가리킨다.

### 묶기 (④)

```bash
uv run python -m tools.make_dataset sessions/20260916_190000
uv run python -m tools.make_dataset sessions/a sessions/b --val 0.2
```

**val 을 무작위로 나누지 않는다.** 수집본은 연사라 앞뒤 장이 거의 같은 그림이다. 무작위로
나누면 train 에 있던 장면이 val 에도 들어가 **점수만 좋아 보이고 실제로는 못 잡는다.**
그래서 회차마다 뒤쪽 연속 구간을 val 로 뗀다.
상자가 하나도 없는 장(배경 표본)은 오탐을 줄여주므로 `--background` 비율만큼만 섞는다.

### 학습 (⑤)

**Colab 무료 GPU 를 권한다.** 이 저장소의 torch 는 CPU 빌드라 300장·100에폭이면 몇 시간이
걸리고, Colab 은 10~20분이면 끝난다. 셀에 코드를 복사하지 않고 **같은 `tools/train.py` 를
그대로 돌린다** — 복사본은 원본과 어긋나기 시작하면 어느 쪽이 맞는지 알 수 없어진다.

```python
# Colab 셀 (런타임 → 런타임 유형 변경 → GPU 먼저)
!pip install -q ultralytics==8.4.48
# dataset/ 을 zip 으로 올리고 푼 뒤, tools/ 와 src/config.py·src/core 도 같이 올린다
!python -m tools.train --data dataset/data.yaml --device 0
# 끝나면 runs/train/weights/best.pt 를 내려받아 models/best.pt 로 넣는다
```

로컬에서 돌린다면:

```bash
uv run python -m tools.train --device cpu --install      # --install 이 models/best.pt 로 복사한다
uv run python -m tools.train --batch 4                   # 메모리 16GB 맥(mps) — 기본 16 이면 넘친다
```

기본값은 `tools/train.py` 머리에 있다 — `EPOCHS 300` · `IMGSZ 1280` · `PATIENCE 100` · `BASE yolo11n.pt`.

- 출발점은 `yolo11n.pt` 다. **처음부터 배우지 않는다** — 수백 장으로는 사물 인식을 새로 만들 수 없고, 이미 사람을 아는 모델을 **모형도 사람으로 보도록 옮기는 것**이 목표다.
- `imgsz` 기본값이 1280 이다. 수집본 원본 크기고, 줄이면 20~30픽셀짜리 모형이 뭉개진다.
- `--batch` 기본 16. **메모리 16GB 맥에서 `imgsz 1280` 이면 4 로 줄인다.**
- `patience 100` — val 이 몇 장뿐이라 점수가 출렁인다. 성급히 멈추지 않으려는 값이다.
- 증강을 기본값보다 세게 준다 (`AUGMENT` = `flipud` · `degrees` · `scale` · `translate` · `hsv_v`).
  장면이 하나뿐이라 모델이 사람의 생김새 대신 이 벽돌벽 · 이 조명을 단서로 삼아버리기 때문이다.
  옵션은 한 번에 하나씩 바꾼다. 다만 **배치를 바꿔 다시 찍는 것이 늘 더 낫다.**

### 반복이 정상이다

한 바퀴로 끝나지 않는다. 100장쯤 라벨해 한 번 학습한 뒤, **그 `best.pt` 로 다음 회차를 사전라벨**하면 초안이 훨씬 정확해져 보정이 빨라진다.

```bash
uv run python -m tools.autolabel sessions/<다음회차> --model models/best.pt
```

## 설정

환경마다 바꾸는 값은 **전부 `src/config.py` 한 곳**에 있다. 다른 파일에 주소·포트·경로를 새로 적지 않는다.

- **경로는 전부 저장소 루트 기준이다.** `sessions/`, `3D_model/`, `models/`, `cameras_imx477_6mm.json` 은 `app.py` 옆에서 찾는다. 절대경로는 코드 어디에도 없다.
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
├── models/                  YOLO 가중치 — best.pt(팀 학습본) 가 있으면 그것, 없으면 yolo11n.pt
├── tools/                   학습 데이터 도구 (앱이 아니다)
│                            autolabel · pick · review · label_gui · labelio · make_dataset · train
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
    │   └── detect.py        YOLO 추론 — YoloDetector(ultralytics) · DetectWorker. 영상은 파이 링크에서 온다
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
uv run python -m tools.autolabel             # 수집본에 YOLO 라벨 초안을 깐다
uv run python -m tools.pick <회차> --count 10 # 라벨할 장 고르기
uv run python -m tools.review <폴더>          # 라벨을 그려서 review/ 에 저장
uv run python -m tools.label_gui <폴더>       # 라벨 도구 (상자 그리기·지우기)
uv run python -m tools.make_dataset <폴더>    # 학습용 dataset/ 구성
uv run python -m tools.train --install       # 파인튜닝 → models/best.pt
uv run pytest                                # 테스트
```

## 남은 일 — GUI 쪽 (2026-09-13 점검, 아직 손대지 않음)

- `pages/mapping/page.py` `_run_odm`: cameras.json 을 줄지 말지를 메모리의 검사 결과로만 정한다. 앱을 다시 켠 뒤 디스크의 선별본으로 바로 제출하면 EXIF 없는 수집본에도 cameras.json 을 안 보낸다. 제출할 이미지에서 `imgcheck.read_exif` 로 직접 판정할 것.
- `pages/mapping/page.py` `_on_odm_failed`: 노드가 uuid 를 모르면(`NodeResponseError`) `odm_uuid.txt` 를 안 지워 그 회차를 재제출할 수 없다. `TaskFailedError` 와 같이 지울 것. `OdmWorker.cancelled` 시그널도 아직 페이지가 받지 않는다 — 받아서 uuid 파일을 지우면 취소 뒤 바로 재제출된다.
- `ui/video.py`: QLabel 에 pixmap 을 넣으면 창을 그 크기 아래로 못 줄인다. `label.setMinimumSize(1, 1)`.
- `pages/detect.py`: 3D 맵 모드 뷰포트에 모델을 올리는 경로가 없다 (3차 보류 중이면 그대로).
- 실시간 탐지는 **파이 영상에서만** 돈다. 파이 없이 노트북 웹캠으로 시험하려면 `camtest.py` 를 따로 쓴다 (저장소 밖).
- `ui/palette.py`: 버튼 hover/pressed 가 앰버(#D97706/#B45309) 그대로다. 파란 계열로 바꿀 때 같이.

### 2026/09/16 수정사항
#### 객체 탐지 관련 학습 내용정리
 - 5개 폴더 안에서 직접 라벨링해서 YOLO 학습좀 시켜놨어요 나머지는 다른 각도에서 찍어서 더 테스트해봐야합니다.

### 2026/09/20 수정사항
#### 파이 경로(MAVLink)로 계기판 채우기

조종기 USB(CRSF) 없이 **파이 중계만으로** 계기판이 차게 했다. 두 경로는 독립이다 —
CRSF 는 `FC → ELRS RX → 조종기 → USB → 지상국`, MAVLink 는 `FC → UART → 파이 → UDP → 지상국`.

**파이 쪽 (배선은 되어 있었고 스위치만 안 켜져 있었다)**
- `aerover-cam.service` 에 `Environment="DRONECAM_FC_SERIAL=/dev/serial0"` 추가. 원본은 파이의 `aerover-cam.service.20260920bak`.
- 로그가 `MAVLink 대기 중 … , FC 중계 /dev/serial0` 으로 바뀌면 켜진 것이다. 이 문구가 없으면 `FC_SERIAL` 이 비어 `open_fc_bridge` 가 `None` 을 돌려준 것.
- 중계 코드는 원래 있었다 (`pi_code` 의 `control/fc_bridge.py` + `programs/stream.py` + `camera_node.forward`). 새로 짠 것은 아래 지상국 핸들러뿐이다.

**지상국 `core/telemetry/mavlink.py` — 핸들러 2개 추가**
- `HEARTBEAT` → `mode`. `base_mode & 0x80`(SAFETY_ARMED)으로 `ARMED` / `DISARMED`. **FC(컴포넌트 1)가 보낸 것만 받는다** — 파이 카메라 노드도 HEARTBEAT 를 보내서, 안 거르면 카메라 상태가 FC 모드를 덮는다.
  - `custom_mode`(INAV 비행모드 번호)는 안 쓴다. disarmed 중 22 로 고정이었고 값의 뜻을 확인하지 못했다.
- `RC_CHANNELS` → `link`. `rssi`(0~254)를 계기판이 쓰는 0~100 으로 줄여 `up_lq` 에 넣는다. **조종기 USB 없이 LINK 칩이 채워진다.** `rssi == 255`(모름)면 갱신하지 않는다.
  - dBm(`up_rssi1`)은 MAVLink 에 없으므로 건드리지 않는다. 없는 값을 채우면 화면이 조용히 거짓말을 한다.

**연결 판정 — 소스별 임계값 (`core/telemetry/status.py`)**
- `link` 에 `source` 필드가 생겼다 (`"crsf"` | `"mavlink"`). 두 파서가 각자 자기 출처를 적는다.
- CRSF 는 0.2초 주기라 `NO_DATA` 기준이 0.3초지만, RC_CHANNELS 는 **실측 0.6Hz · 간격 1.0~4.2초** 다. 같은 기준을 쓰면 값이 멀쩡히 들어와도 계속 `NO_DATA` 로 읽힌다. 그래서 `MAVLINK_NO_DATA_SEC = 5.0` 을 따로 둔다.
- 출처가 비어 있으면 엄한 쪽(CRSF)을 쓴다. CRSF 단독 동작은 그대로다.
- CRSF 도 `source` 를 적어야 한다. 안 적으면 MAVLink 가 한 번 박아둔 `"mavlink"` 가 남아, USB 를 꽂아도 느슨한 5초 기준이 계속 적용된다.

#### 실측 기록 (2026-09-20, SpeedyBee F405 V4 + INAV)

- `/dev/serial0` **57600**. 115200 으로 읽으면 깨진다. 정상이면 `fd`(MAVLink v2 매직)로 시작한다.
- 6초 동안: SYS_STATUS 11 · ATTITUDE 11 · VFR_HUD 11 · RC_CHANNELS 6 · BATTERY_STATUS 6 · SCALED_PRESSURE 6 · STATUSTEXT 6 · HEARTBEAT 6 · SYSTEM_TIME 5. srcSystem 1 / srcComponent 1.
- 배터리 6S 22.86V · 0.48A · 56% (셀 3.81V). `SYS_STATUS.voltage_battery` 가 0 이면 전압 스케일이 아니라 **배터리 팩이 빠진 것**부터 의심할 것.
- 조종 링크 상태 판별:

  | | 끊김 | 살아 있음 |
  |---|---|---|
  | `RC_CHANNELS.rssi` | 0 | 254 |
  | 채널 값 | 전 채널 고정 (페일세이프) | 스틱 따라 움직임 |
  | `SYS_STATUS` RC_RECEIVER | `health=False` | `health=True` |
  | `STATUSTEXT` | `NO RC LINK`, `UNABLE TO ARM` | 없음 |

  **메시지가 온다는 것만으로는 조종 신호가 살아 있다는 뜻이 아니다.** 끊겨도 페일세이프 값이 계속 온다.
- 스로틀은 `VFR_HUD.throttle` 이 FC 자신의 출력이라 채널 맵 추측이 필요 없다. RC 링크가 없으면 `65535`(int16 으로 -1 = 무효)가 온다.
- 어느 RC 채널이 스로틀인지는 FC 의 `map` 설정에 달렸고 **MAVLink 만으로는 알 수 없다.** 스로틀 스틱만 움직이며 어느 `ch` 가 따라가는지 봐야 한다.

#### GUI 개편 사항

콘티와 어긋나 있던 부분과, 화면에서 빈 공간만 차지하던 부분을 고쳤다.
화면 구성은 Designer(.ui) 가 아니라 **코드로만** 짠다 — 커스텀 위젯(뷰포트·평면도·
계기판)이 대부분이라 .ui 로 옮겨도 얻는 것이 없다.

**셸 (`main_window.py`)**
- 좌측 사이드바 190px → **64px 아이콘 레일**. 로고·부제목을 없애고 페이지 이름은 툴팁으로 보낸다.
- 아이콘은 `icon/*.svg` (Material Symbols). SVG 의 `fill` 만 바꿔 두 벌을 그려 `QIcon` 의 On/Off 에 넣는다 — 선택 안 된 것은 흐리게, 선택된 것은 진하게. 레티나에서 뭉개지지 않게 3배로 그린다.
  - `QPushButton` 아이콘의 Active 모드는 호버가 아니라 **포커스**다. 호버 강조는 QSS 배경으로 한다.

**드론 상태 (`pages/status.py`)**
- 상단 HUD 를 떠 있는 둥근 카드에서 **영상 윗변에 붙은 직사각 반투명 바**(`QFrame#hudBar`)로 바꿨다. 상세 텔레메트리 패널은 그대로 떠 있는 카드다.
- 칩에 상태 아이콘을 붙였다 (`ui/hud_icons.py`, 전부 `QPainter` 로 직접 그린다 — 상태별 SVG 를 두지 않는다).
  - **BATT**: 5칸 레벨. 한 칸이 20% 라 경고(40%)·위험(20%) 경계가 칸 경계와 맞는다. 올림이라 1% 여도 한 칸은 남는다.
  - **LINK**: LQ 25 마다 신호 막대 1칸. **LOST(LQ 0) 는 빈 막대 윤곽을 빨강으로** 그려 미수신과 구분한다.
  - **GPS**: 조준경. 가운데 점은 `sats >= 6` 일 때만 — `HOME 설정` 버튼이 열리는 조건과 같다.
  - 판정과 색은 페이지가 정하고 아이콘은 받은 값을 그리기만 한다.
- 하단 제어부를 **한 줄 3구역(연결 | 진행 | 수집)** 으로 재구성. 가운데 늘어나는 칸이 예전엔 빈 공간이었는데, 지금은 진행바와 모든 안내문이 거기 뜬다. 두 번째 줄이 없어져 영상이 그만큼 커졌다.
  - `파이`·`간격`·`목표` 라벨을 지우고 상태 점(●)과 입력칸 prefix 로 대신한다.
  - 해상도·fps 는 영상 왼쪽 아래 **OSD** 로 옮겼다.

**3D 매핑 (`pages/mapping/`)**
- 스텝 카드를 `번호 · 제목 · 짧은 상태` + **카드 폭 전체 버튼**으로 바꿨다. 250px 패널에서 버튼을 오른쪽에 두면 제목과 상태가 눌려 두 줄로 접혔다.
- 카드 문구를 `85장`, `검사 완료`, `경고 2건`, `60%` 수준으로 줄이고 전문은 툴팁으로 보냈다.
- 패널 아래 빈 공간 → **기록 카드**. 검사 요약·경고·오류·저장 경로가 시간과 함께 쌓인다(최대 100줄). ODM 이 29분 걸려서 "아까 뭐라고 떴었지" 를 되짚을 수 있어야 한다. 진행률처럼 자주 바뀌는 값은 넣지 않는다.
- 뷰어 우하단 플로팅 버튼: **확대 · 축소 · 탑뷰 · 시점 초기화**. 글리프만 두고 이름은 툴팁. 확대/축소는 휠과 같은 `camera.zoom()` 을 쓰고 버튼 한 번은 휠 두 칸이다.
  - 탑뷰는 pitch 89도. 90도면 up 벡터가 무너진다 (orbit 한계각과 같은 값).

**요구조자 탐지 (`pages/detect.py`)**
- 상단 HUD 도 같은 붙은 바로 통일. 탐지 목록·좌표계 경고는 떠 있는 카드 그대로.
- 신뢰도 슬라이더에 QSS 를 입혔다. 기본 스타일은 자기 배경을 칠해 바 위에서 얼룩졌다.

**회차 선택에 의미 주기**
- **검사 결과 캐시 (`core/checkcache.py`)**: 통과한 검사 결과를 `3D_model/<회차>/check.json` 에 저장하고, 회차를 고를 때 되살린다. ② 서브샘플은 ① 이 잰 블러 값이 있어야 도는데, 예전엔 앱을 껐다 켜면 그 값이 사라져 446장 × 46초를 매번 다시 썼다.
  - 낡음 판정은 입력 폴더의 **파일별 (이름, 크기, 수정시각)** 지문. 장수만 보면 한 장을 바꿔치기한 경우를 놓친다.
  - 취소된 보고서는 판정을 안 한 것이라 저장하지 않는다.
- **회차 목록에 단계 표시**: `20260919_164512 — ③ ODM 완료` 처럼. 판단 근거는 산출물 폴더의 파일뿐이다 (`model_cropped.glb` → ④, `model.glb` → ③, `odm_uuid.txt` → ③ 진행 중, `sub/` → ②, `check.json` → ①). 고르기 전에 목록에서 비교할 수 있다.

**데이터 정리 (2026-09-20)**
- `3D_model/` 854M → 19M, `sessions/` 26M → 19M. 남긴 것은 뷰어 시험용 모델 `3D_model/20260909_195610/` 과 최근 촬영본 `sessions/20260919_163912`, `20260919_164512` 뿐이다.
- 지운 것: `3D_model/{test, images, images_sub, 20260907_205517}`, `sessions/20260915_182245`, `sessions/20260916_*` 5개(라벨 `.txt` 87개).

#### 남은 것

- **GPS 배선.** `GPS_RAW_INT` 가 안 와서 계기판 GPS · SPD · HOME 행이 비고 `HOME 설정` 버튼이 비활성이다. 매핑에 HOME 이 필요하면 이게 다음 블로커다.
- `RC_CHANNELS.rssi` 가 INAV 의 어떤 RSSI 소스를 반영하는지 미확인. 실측에서는 CRSF LQ 100 과 rssi 254 가 일치했다.
- **GUI**: 회차 목록이 `sessions/` 만 본다. 외부 폴더를 ① 에서 직접 고른 회차는 `3D_model/` 에 결과가 남아도 목록에 없다 (`source.txt` 로 이어붙일 수는 있다).
- **GUI**: 탐지 페이지에는 뷰어 줌 버튼이 없다. 3D 맵 모드에서는 매핑 페이지와 같은 플로팅 버튼이 있는 편이 낫다.
- 조종기 USB(CRSF)는 여전히 쓸모가 있다. 파이 경로는 Wi-Fi 에 종속이라, Wi-Fi 가 끊기면 계기판 전체가 죽는다. CRSF 는 ELRS 경로라 독립이고 보통 더 멀리 간다 — 야외에서는 둘 다 두는 편이 안전하다.
