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
조종기 USB ──CRSF 텔레메트리 미러────────────────────────────────────▶ aerover 계기판
aerover ──HTTP──▶ NodeODM ──▶ model.glb ──▶ aerover 3D 매핑 · 탐지 페이지
```

- 영상은 **RTP (RFC 3550) 위의 RTP/JPEG (RFC 2435)** 다. 표준이라 GStreamer · VLC · ffmpeg 로도 받는다.
- 제어와 텔레메트리는 **MAVLink v2** 다. 지상국 HEARTBEAT 가 끊기면 파이가 스스로 스트림을 멈춘다.
- 옛 TCP 방식(길이 4바이트 + JPEG, 포트 5001)은 파이 `app.py tcp` 와 지상국 `AEROVER_LINK=tcp` 로 남아 있다.
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
| `AEROVER_PI_HOST` | aerover | `192.168.137.70` | 파이 주소 |
| `AEROVER_LINK` | aerover | `rtp` | 영상 링크 방식 `rtp`(UDP) / `tcp`(옛 방식) |
| `AEROVER_MAVLINK_PORT` / `AEROVER_RTP_PORT` | aerover | `14550` / `5004` | 파이 MAVLink 포트 / 지상국이 RTP 를 받는 포트 |
| `AEROVER_PI_PORT` | aerover | rtp 면 `14550`, tcp 면 `5001` | 화면 "호스트:포트" 칸 기본값 |
| `AEROVER_SERIAL` | aerover | OS 별 (윈도우 `COM5`, 맥 `/dev/tty.usbmodem*`, 리눅스 `/dev/ttyACM0`) | 조종기 USB 포트 |
| `AEROVER_ODM_HOST` / `AEROVER_ODM_PORT` | aerover | `localhost` / `3000` | NodeODM |
| `DRONECAM_MAVLINK_PORT` / `DRONECAM_RTP_PORT` | pi_code | `14550` / `5004` | 위와 같아야 한다 |
| `DRONECAM_FC_SERIAL` / `DRONECAM_FC_BAUD` | pi_code | (없음) / `57600` | FC 의 MAVLink UART — 주면 텔레메트리를 중계한다 |
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
cd aerover && uv run pytest        # 66개 — CRSF · RTP/JPEG · MAVLink · 텔레메트리 · 검사 · ODM 작업 · Qt 워커(가짜 파이)
cd pi_code && python -m pytest     # 36개 — 가짜 picamera2 로 조립까지 확인. 실제 카메라는 파이에서
```

## 건드리지 않은 것

- `aerover/src/pages/`, `ui/`, `gl/`, `main_window.py`, `app.py` — GUI. 디자인 작업 중이라
  그대로 둔다. 예외는 `pages/mapping/page.py` 의 `_out_dir` 한 곳 — 절대경로를 적던 부분을
  `core/workspace.py` 로 넘기는 3줄이다. UDP 전환은 화면 코드를 바꾸지 않았다 (링크 시그널이 같다).
- `pi_code/cam_server.py`, `daemons/daemon.py`, `imagesend*.py`, `stream_send.py`,
  `image_interval.py`, `secound.py` — 파이에 있던 **원본 백업**(SHA-256 대조본).
  절대경로가 남아 있지만 실행 코드가 아니라 보관본이다. 실행은 `app.py <명령>` 으로 한다.

## 가상환경 · 용량

가상환경은 저장소에 두지 않는다. 만든 컴퓨터의 바이너리라 다른 곳에서 안 돌고, 용량만 차지한다
(2026-09-13 에 `aerover/.venv` 1.4GB 와 `pi_code/droneenv` 287MB 를 지웠다). 각자 만든다:

| 프로젝트 | 만드는 법 | 버전 고정 |
|---|---|---|
| aerover | `uv sync` (또는 `pip install -r requirements.txt`) | `pyproject.toml` · `uv.lock` · `requirements.txt` 가 같은 버전 |
| pi_code | `python3 -m venv --system-site-packages droneenv` 후 `pip install -r requirements.txt` | picamera2 등은 apt 패키지 — `requirements.txt` 머리말 참고 |

`.gitignore` 가 `.venv/` · `droneenv/` · `__pycache__/` · `pi_code/run/` 을 제외한다.
그 밖에 큰 것: `pi_code/rec/`(녹화본 566MB) · `aerover/3D_model/`(110MB) · `pi_code/test.jpg`(1.2MB). 데이터라 지우지 않았다 — 공유할 때 빼거나 따로 보관한다.
