# EXPO_code — AeroVer 재난 구조 드론

두 개의 프로그램이 한 쌍이다. 변경 이력은 [CHANGELOG.md](CHANGELOG.md).

| 폴더 | 어디서 도나 | 무엇을 하나 |
|---|---|---|
| [`aerover/`](aerover/README.md) | 지상국 노트북 (PySide6 GUI) | 드론 상태 · 3D 매핑 · 요구조자 탐지 |
| [`pi_code/`](pi_code/README.md) | 기체의 라즈베리파이 | 카메라 스트림 · 녹화 · 웹 미리보기 |

```
지상국 ──MAVLink/UDP 14550: HEARTBEAT · VIDEO_START/STOP_STREAMING──▶ 파이  app.py stream
지상국 ◀──MAVLink: HEARTBEAT · ACK · STATUSTEXT · (FC 텔레메트리 중계)──── 파이
지상국 ◀──RTP/JPEG (UDP 5004)──────────────────────────────────────── 파이
FC ──MAVLink UART (/dev/serial0 57600)──▶ 파이 ──위 UDP 로 중계──────▶ aerover 계기판
조종기 USB ──CRSF 텔레메트리 미러────────────────────────────────────▶ aerover 계기판
aerover ──HTTP──▶ NodeODM ──▶ model.glb ──▶ aerover 3D 매핑 · 탐지 페이지
```

- 영상은 **RTP (RFC 3550) 위의 RTP/JPEG (RFC 2435)** 다. 표준이라 GStreamer · VLC · ffmpeg 로도 받는다.
- 제어와 텔레메트리는 **MAVLink v2** 다. 지상국 HEARTBEAT 가 끊기면 파이가 스스로 스트림을 멈춘다.
- 옛 TCP 방식(길이 4바이트 + JPEG, 포트 5001)은 파이 `app.py tcp` 와 지상국 `AEROVER_LINK=tcp` 로 남아 있다.
- 계기판을 채우는 경로가 **둘이고 서로 독립이다.** 조종기 USB(CRSF)와 파이 중계(MAVLink)가 같은 칸에 들어간다.
  조종기 USB 없이도 파이 경로만으로 배터리 · 모드 · 자세 · 고도 · 링크가 찬다 (2026-09-20). 자세한 것은 aerover/README 의 "2026/09/20 수정사항".
- 양쪽에 **같은 파일**이 있다: `rtpjpeg.py` (RTP/JPEG 코덱). 한쪽을 바꾸면 다른 쪽도 바꾼다 — 테스트가 대조한다.

## 빠른 시작

```bash
# 지상국 (uv 권장. 없으면 aerover/requirements.txt 로 pip)
cd aerover
uv sync
uv run python app.py

# 파이 (~/drone 에 pi_code 를 통째로 둔다. venv 는 pi_code/requirements.txt 머리말대로 만든다)
cd ~/drone
python3 -m venv --system-site-packages droneenv && droneenv/bin/pip install -r requirements.txt
droneenv/bin/python app.py stream
```

## 모형 학습 · 라벨링 (요구조자 탐지)

사전학습 `yolo11n.pt` 는 **진짜 사람은 잘 잡지만 모형은 거의 못 잡는다.** 시연은 모형으로 하므로
우리 수집본으로 다시 가르친다. 도구는 전부 [`aerover/tools/`](aerover/tools/) 에 있고 (앱이 아니라
사람이 쓰는 CLI 라 `src/` 밖에 둔다), 결과물 `models/best.pt` 를 앱 탐지 페이지가 집는다.

```
① 수집  →  ② 사전라벨  →  ③ 고르기  →  ④ 확인  →  ⑤ 보정  →  ⑥ 묶기  →  ⑦ 학습  →  ⑧ 투입
앱 수집     autolabel      pick        review     label_gui    make_dataset  train     앱 재시작
```

명령은 전부 `apps/aerover/` 에서 돈다.

```bash
cd apps/aerover && uv sync              # 처음 한 번 (PySide6 가 있어야 라벨 도구 창이 뜬다)
uv run python -m tools.autolabel sessions/<회차>
uv run python -m tools.pick      sessions/<회차> --count 10
uv run python -m tools.review    label/<회차>_r1        # 빠진 자리를 눈으로 확인
uv run python -m tools.label_gui label/<회차>_r1        # ← 진짜 일은 여기다
uv run python -m tools.make_dataset label/<회차>_r1
uv run python -m tools.train --install                 # → models/best.pt
```

**단축키 · 폴더 구조 · 각 단계를 왜 그렇게 하는지는 전부
[aerover/README.md](aerover/README.md) 의 "모형 학습 — best.pt 만들기" 절에 있다.**
여기에 옮겨 적지 않는다 — 두 곳에 두면 한쪽만 고쳐져 어긋난다.

## 경로 · 설정 규칙

- **하드코딩된 절대경로가 없다.** 모든 경로는 각 프로젝트의 `src/config.py` 가
  저장소 루트(`ROOT`, `app.py` 가 있는 곳)를 기준으로 계산한다. 폴더를 통째로 옮기거나
  맥 ↔ 윈도우 ↔ 파이를 오가도 같은 자리를 가리킨다.
- 산출물이 원본을 기억해야 할 때(`aerover/3D_model/<회차>/source.txt`)도 **루트 기준
  상대경로**로 적는다. 다른 컴퓨터에서 적힌 옛 기록은 폴더명으로 맞춘다.
- 자리마다 다른 값(장비 IP · 포트 · 시리얼 포트 · 소켓 위치)은 **환경변수로 덮어쓴다.**
  코드를 고치지 않는다.

| 환경변수 | 프로젝트 | 기본값 | 뜻 |
|---|---|---|---|
| `AEROVER_PI_HOST` | aerover | 의현 노트북(MUYAHOO): `192.168.137.70`, 인우 노트북(MUYAHO) : `192.168.137.68` | 파이 주소. **망이 바뀌면 달라진다** — 파이에서 `hostname -I` 로 확인 (2026-09-20 에는 `10.53.199.81` 이었다) |
| `AEROVER_LINK` | aerover | `rtp` | 영상 링크 방식 `rtp`(UDP) / `tcp`(옛 방식) |
| `AEROVER_MAVLINK_PORT` / `AEROVER_RTP_PORT` | aerover | `14550` / `5004` | 파이 MAVLink 포트 / 지상국이 RTP 를 받는 포트 |
| `AEROVER_PI_PORT` | aerover | rtp 면 `14550`, tcp 면 `5001` | 화면 "호스트:포트" 칸 기본값 |
| `AEROVER_SERIAL` | aerover | OS 별 (윈도우 `COM5`, 맥 `/dev/tty.usbmodem*`, 리눅스 `/dev/ttyACM0`) | 조종기 USB 포트 |
| `AEROVER_ODM_HOST` / `AEROVER_ODM_PORT` | aerover | `localhost` / `3000` | NodeODM |
| `DRONECAM_MAVLINK_PORT` / `DRONECAM_RTP_PORT` | pi_code | `14550` / `5004` | 위와 같아야 한다 |
| `DRONECAM_FC_SERIAL` / `DRONECAM_FC_BAUD` | pi_code | (없음) / `57600` | FC 의 MAVLink UART — 주면 텔레메트리를 중계한다. 파이 `aerover-cam.service` 에는 `/dev/serial0` 로 넣어 뒀다 (2026-09-20) |
| `DRONECAM_STREAM_PORT` | pi_code | `5001` | 옛 TCP 스트림 포트 |
| `DRONECAM_SOCK` | pi_code | `<루트>/run/control.sock` | 녹화 데몬 제어 소켓 |
| `DRONECAM_DEST_IP` / `DRONECAM_DEST_PORT` | pi_code | `192.168.0.177` / `9999` | 인터벌 UDP 전송 대상 |
| `DRONECAM_WEB_HOST` / `DRONECAM_WEB_PORT` | pi_code | `0.0.0.0` / `8000` | 웹 미리보기 |
| `EXP` / `GAIN` | pi_code | (자동 노출) | 노출 고정 (마이크로초 · 게인) |

## 코드 구조 원칙

두 프로젝트 모두 같은 규칙으로 짜여 있다.

- **한 모듈 = 한 책임.** 파싱·판정·저장·전송·스레드가 각각 다른 파일이다.
  예: 텔레메트리는 `crsf.py`(파싱) · `mavlink.py`(MAVLink 매핑) · `state.py`(보관) · `status.py`(판정) ·
  `home.py`(거리) · `receiver.py`(수신 스레드) 로 나뉜다. 영상 링크는 `rtpjpeg.py`(코덱) · `gcs.py`(MAVLink) ·
  `link.py`(스레드) 다.
- **확장은 표에 한 줄.** 새 CRSF 프레임은 `crsf.FRAMES`, 새 MAVLink 메시지는 `telemetry.mavlink.HANDLERS`,
  새 검사 규칙은 `imgcheck.RULES`, 새 파이 명령은 `app.PROGRAMS` 에 항목을 더한다. 기존 루프는 손대지 않는다.
- **의존은 주입한다.** 순수 로직은 소켓·카메라·Qt 를 직접 만들지 않고 호출 가능한
  객체나 `Protocol` 로 받는다 (`GcsEndpoint(send)`, `CameraNode(send, on_start, on_stop)`,
  `SerialReader(open_port)`, `OdmJob(node, sink, is_cancelled)`, `Recorder(camera, make_encoder, make_output)`,
  `DetectWorker(detector)`). 그래서 진짜 장비 없이 테스트한다.
- **Qt 는 어댑터에만.** `RtpLinkWorker` · `LinkWorker` · `DetectWorker` · `OdmWorker` 는 순수 로직을
  스레드에서 돌리고 시그널로 바꾸는 얇은 껍데기다.
- **의존 방향은 한쪽.** aerover 는 `pages → core · gl · ui`, pi_code 는
  `app.py → programs → 부품 패키지`. 부품끼리는 서로 조립하지 않는다.

## 테스트

```bash
cd aerover && uv run pytest        # 88개 — CRSF · RTP/JPEG · MAVLink · 텔레메트리 · 검사 · ODM 작업 · Qt 워커(가짜 파이)
cd pi_code && python -m pytest     # 36개 — 가짜 picamera2 로 조립까지 확인. 실제 카메라는 파이에서
```

## 건드리지 않은 것

- ~~`aerover/src/pages/`, `ui/`, `gl/`, `main_window.py` — GUI~~ **2026-09-19~20 에 디자인 작업이 들어갔다.**
  드론 상태 페이지(사이드바 아이콘 레일 · HUD 바 · `ui/hud_icons.py` · 하단 제어부), 3D 매핑 페이지(스텝 카드 ·
  로그 카드 · 뷰어 플로팅 버튼). 여기 적힌 "그대로 둔다"는 더 이상 맞지 않는다.
  - 구조 원칙은 그대로다 — 링크 시그널이 같아서 UDP 전환은 화면 코드를 안 바꿨고,
    절대경로는 `pages/mapping/page.py` 의 `_out_dir` 에서 `core/workspace.py` 로 넘어가 있다.
- `pi_code/cam_server.py`, `daemons/daemon.py`, `imagesend*.py`, `stream_send.py`,
  `image_interval.py`, `secound.py` — 파이에 있던 **원본 백업**(SHA-256 대조본).
  절대경로가 남아 있지만 실행 코드가 아니라 보관본이다. 실행은 `app.py <명령>` 으로 한다.

## 가상환경 · 용량

가상환경은 저장소에 두지 않는다. 만든 컴퓨터의 바이너리라 다른 곳에서 안 돌고, 용량만 차지한다
(2026-09-13 에 `aerover/.venv` 1.4GB 와 `pi_code/droneenv` 287MB 를 지웠다). 각자 만든다:

| 프로젝트 | 만드는 법 | 버전 고정 |
|---|---|---|
| aerover | `uv sync` (또는 `pip install -r requirements.txt`) | `pyproject.toml` · `uv.lock` · `requirements.txt` 가 같은 버전 |
| pi_code | `python3 -m venv --system-site-packages droneenv` 후`pip install -r requirements.txt` | picamera2 등은 apt 패키지 — `requirements.txt` 머리말 참고 |

`.gitignore` 가 `.venv/` · `droneenv/` · `__pycache__/` · `pi_code/run/` 을 제외한다.
그 밖에 큰 것: `pi_code/rec/`(녹화본 566MB) · `aerover/3D_model/`(110MB) · `pi_code/test.jpg`(1.2MB). 데이터라 지우지 않았다 — 공유할 때 빼거나 따로 보관한다.
