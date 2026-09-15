# 변경 이력

## 2026-09-13 — 통신 계층 UDP 전환: MAVLink 제어 + RTP/JPEG 영상

TCP 로 JPEG 를 이어 보내던 파이 ↔ 지상국 링크를 **UDP** 로 바꿨다. 제어와 텔레메트리는
**MAVLink v2**, 영상은 **RTP (RFC 3550) 위의 RTP/JPEG (RFC 2435)** 다.

```
지상국 ──HEARTBEAT · VIDEO_START_STREAMING (MAVLink/UDP 14550)──▶ 파이
지상국 ◀──MAVLink: HEARTBEAT · COMMAND_ACK · STATUSTEXT · FC 텔레메트리 중계── 파이
지상국 ◀──RTP/JPEG (UDP 5004)───────────────────────────────────── 파이
```

- 지상국이 붙으면 HEARTBEAT 와 `VIDEO_START_STREAMING` 을 보내고, 파이는 그 지상국 주소로
  RTP 를 쏜다. 끊을 때는 `VIDEO_STOP_STREAMING`. 지상국 HEARTBEAT 가 3초 끊기면 파이가
  스스로 멈추고 카메라를 놓는다 — TCP 때 Wi-Fi 가 끊기면 소켓이 수 분간 붙잡히던 문제가 사라졌다.
- RTP/JPEG 는 JPEG 헤더를 벗기고 스캔 데이터만 보내 받는 쪽이 표준 허프만 테이블로 되살린다.
  픽셀은 원본과 동일하다 (테스트로 확인). 패킷이 하나라도 빠진 프레임은 버린다 — 재전송 없음.
  ODM 에 넣는 수집본은 여전히 JPEG 원본 그대로다.
- 표준 규격이라 GStreamer(`rtpjpegdepay`) · VLC · ffmpeg 로도 받을 수 있고, MAVLink 는 QGroundControl
  같은 지상국 소프트웨어와 호환된다.
- FC 텔레메트리: 파이가 FC 의 MAVLink UART 를 읽어 같은 UDP 로 중계하고(`DRONECAM_FC_SERIAL`),
  지상국 계기판이 GPS_RAW_INT · ATTITUDE · SYS_STATUS · BATTERY_STATUS · VFR_HUD 를 CRSF 와 같은
  키에 넣는다. **FC ↔ 파이 배선은 아직 없어 준비만 된 상태**다. 조종기 USB(CRSF) 경로는 그대로다.
- 옛 TCP 방식은 남겼다: 파이 `app.py tcp`, 지상국 `AEROVER_LINK=tcp`.

### aerover
- 새 파일 `src/core/rtpjpeg.py` (RTP/JPEG 패킷화·재조립, 파이와 동일 파일), `src/core/gcs.py`
  (지상국 MAVLink 끝점), `src/core/telemetry/mavlink.py` (MAVLink 메시지 → 계기판 상태).
- `src/core/link.py` — `RtpLinkWorker` 추가(기본), 기존 TCP `LinkWorker` 유지. `LinkHub` 가
  설정에 따라 워커를 고른다. 화면 코드는 바꾸지 않았다 — 시그널이 같다.
- `src/config.py` — `LINK_MODE` · `MAVLINK_PORT` · `RTP_PORT` · `TCP_PORT`. `PI_PORT` 기본값은 rtp 면
  14550, tcp 면 5001.
- `src/core/telemetry/receiver.py` — 공유 상태 보관함 접근자 `store()`.
- 의존성 `pymavlink==2.4.49` 추가. 테스트 66개 (RTP/JPEG 왕복 · 유실 · 순서 뒤바뀜, MAVLink 끝점,
  가짜 파이와의 UDP 워커 통합, 파이와 파일 대조).

### pi_code
- 새 파일 `src/stream/rtpjpeg.py` (aerover 와 동일), `src/stream/rtp_output.py` (인코더 → RTP/UDP),
  `src/stream/rtp_session.py` (지상국 주소별 카메라 수명), `src/control/camera_node.py`
  (MAVLink 카메라 노드), `src/control/fc_bridge.py` (FC UART → UDP 중계), `src/programs/tcp.py`
  (옛 TCP 서버).
- `src/programs/stream.py` — UDP 링크로 교체. `app.py` 명령표에 `tcp` 추가.
- `src/config.py` — `MAVLINK_PORT` · `RTP_PORT` · `RTP_MTU` · `GCS_TIMEOUT_SEC` · `FC_SERIAL` · `FC_BAUD`.
- 의존성 `pymavlink==2.4.49`, `pyserial==3.5`(FC 중계 시). 테스트 36개 + 1 건너뜀.

### 아직 안 한 것
- 파이 실기: 실제 카메라로 RTP 송신, Wi-Fi 에서의 프레임 유실률, FC UART 배선 후 MAVLink 중계.
- 파이의 systemd 유닛(`aerover-cam.service`)은 아직 원본 `cam_server.py`(TCP)를 띄운다.
  UDP 로 바꾸려면 유닛의 실행 명령을 `app.py stream` 으로 바꿔야 한다.

## 2026-09-13 — 정리: 절대경로 제거 · SOLID 구조 · 테스트 · README

- **절대경로 제거.** 두 프로젝트의 모든 경로가 저장소 루트(`ROOT`) 기준이다. 장비 주소·시리얼 포트·
  소켓 위치는 `AEROVER_*` / `DRONECAM_*` 환경변수로 덮어쓴다. `3D_model/<회차>/source.txt` 는
  상대경로를 적는다 (새 모듈 `aerover/src/core/workspace.py`). 기존 Mac 경로 기록 3개를 고쳤다.
- **aerover core 재구성.** 텔레메트리를 파싱·상태·판정·HOME·수신 스레드로 분리, `imgcheck` 를
  검사기·규칙표·순회로 분리, ODM 을 순수 `OdmJob` 과 Qt `OdmWorker` 로 분리, 전송 규약을
  `framing.py` 로 분리, 탐지기 주입(`Detector`). 확장은 표(`crsf.FRAMES` · `imgcheck.RULES`)에 한 줄.
- **고친 버그.** ODM 취소·예외가 화면에 전달되지 않던 것, EXIF 오경고와 매칭 인덱스 어긋남,
  연결 직전 해제 요청 유실, HOME 경도 축척 서울 고정, 파이 스트림 소켓 타임아웃·keepalive 없음.
- **pi_code.** 제어 소켓을 `run/control.sock` 으로(sudo 불필요), 출력을 콜백·log 로 통일,
  `os.sync` 방어, 로그 파일 UTF-8. 원본 백업 스크립트는 손대지 않았다.
- **GUI 는 손대지 않았다.** 예외는 `pages/mapping/page.py` `_out_dir` 의 3줄 (절대경로 제거에 필요).
  GUI 쪽 남은 문제는 `aerover/README.md` "남은 일".
- **테스트.** aerover 46개, pi_code 23개 + 1 건너뜀 (가짜 picamera2). **README** 루트 신설, 두 프로젝트 갱신.
- **가상환경 삭제.** `aerover/.venv`(1.4GB, 맥 바이너리) · `pi_code/droneenv`(287MB, 파이 바이너리).
  `requirements.txt` 두 개와 `.gitignore` 추가. 전체 2.4GB → 717MB.

### 새로 추가한 항목

* **`aerover/requirements.txt`**: `pyproject`와 동일하게 버전을 고정하였다. `uv` 없이도 `pip install -r requirements.txt` 명령으로 설치할 수 있다.
* **`pi_code/requirements.txt`**: `pip`로 설치하는 패키지는 `opencv-python` 하나뿐이다. `picamera2`, `libcamera`, `PIL`, `numpy`, `gpiozero`, `flask`는 `apt` 패키지이므로, 파일 머리말에 설치 순서와 `--system-site-packages` 옵션을 사용하여 가상환경(venv)을 생성하는 명령을 명시하였다.
* **`.gitignore`**: `.venv/`, `droneenv/`, `__pycache__/`, `pi_code/run/` 디렉터리를 제외하였다. 추후 `git` 사용 시 가상환경 파일이 깃에 포함되지 않는다.
* **문서 업데이트**: 세 개의 `README` 파일 실행 섹션에 가상환경(venv) 생성 방법을 추가하였다.

### 이번 세션 전체 변경 사항

#### aerover — 새 파일

* **`src/core/framing.py`**: 길이 4바이트 및 JPEG 규약을 처리하며, `FrameReader`를 포함한다. 소켓과 Qt에 의존하지 않는다.
* **`src/core/workspace.py`**: `3D_model/<회차>/` 디렉터리를 탐색하고 생성한다. `source.txt`에 상대경로를 기록한다.
* **`src/core/telemetry/state.py, status.py`**: 상태 보관함과 연결 판정 로직을 `receiver`에서 분리하였다.
* **`tests/`**: 8개의 파일과 46개의 테스트 코드를 추가하였다.

#### aerover — 수정된 파일

* **`src/config.py`**: 모든 경로를 `ROOT` 기준으로 변경하였다. 시리얼 포트는 OS별 기본값을 사용하되 `AEROVER_SERIAL` 환경변수로 덮어쓸 수 있게 하였다. 라즈베리파이와 NodeODM 주소 또한 환경변수로 변경하였다.
* **`src/core/telemetry/crsf.py`**: 프레임 종류를 `FRAMES` 표 하나로 통합하고, `build_frame` 함수를 추가하였다.
* **`src/core/telemetry/receiver.py`**: 전역 함수를 `SerialReader` 스레드 클래스로 대체하였으며, 포트 개방 방식은 주입받도록 수정하였다.
* **`src/core/telemetry/home.py`**: `HomePoint` 클래스를 추가하였다. 경도 축척을 서울 고정 방식에서 `HOME` 위도 기준으로 변경하였다.
* **`src/core/link.py`**: 프레이밍 로직을 `FrameReader`로 위임하였다. 연결 직전에 발생한 해제 요청이 유실되던 버그를 수정하였다.
* **`src/core/detect.py`**: 탐지기를 주입받도록 `Detector` 프로토콜을 도입하였고, 정지 플래그를 `Event` 객체로 변경하였다.
* **`src/core/imgcheck.py`**: `ImageInspector`와 `RULES`로 분리하였다. EXIF 오경고 현상과 매칭 인덱스가 어긋나는 문제를 수정하였다.
* **`src/core/odm.py`**: 순수 `OdmJob`과 Qt 어댑터인 `OdmWorker`로 분리하였다. `cancelled` 시그널과 전체 예외 포착 로직을 추가하였다.
* **`src/core/cropper.py`**: 상자 판정 로직을 `CropBox`로 분리하고, UV 주석을 정정하였다.
* **`src/core/capture.py, subsample.py`**: 상수를 정리하고 빈 목록에 대한 방어 로직을 추가하였다.
* **`src/pages/mapping/page.py`**: GUI 부분의 유일한 수정 사항으로, `_out_dir` 본체를 `ModelStore` 호출 한 줄로 단축하였다.
* **`3D_model/*/source.txt` (총 3개)**: Mac 환경의 절대경로를 상대경로로 수정하였다.
* **`pyproject.toml`**: `pytest` 설정 및 `dev` 의존성을 추가하였다.

#### pi_code — 수정된 파일

* **`src/config.py`**: 소켓 경로를 `run/control.sock`으로 변경하고, `DRONECAM_*` 환경변수를 도입하였다.
* **`src/stream/tcp_server.py`**: 송신 타임아웃을 5초로 설정하고 `keepalive` 기능을 추가하였다. `print` 문을 `log`로 대체하였다.
* **`src/stream/socket_output.py`**: 연결이 끊긴 후에는 데이터를 보내지 않도록 처리하고, `write`가 데이터 길이를 반환하도록 수정하였다.
* **`src/stream/udp_sender.py`**: 출력 로직을 `on_sent` 콜백으로 분리하고, `KeyboardInterrupt` 처리 로직을 프로그램 내부로 이동하였다.
* **`src/control/unix_server.py`**: `Path` 객체를 기반으로 코드를 정리하였다.
* **`src/recorder/recorder.py, report.py`**: 리눅스 전용 `os.sync`에 대한 방어 로직을 추가하고, 로그 파일을 UTF-8 인코딩으로 설정하였다.
* **`src/programs/stream.py, udp.py, record.py`, `src/control/cli.py`, `src/camera/settings.py`, `app.py**`: 로그 형식을 통일하고, 사용법(usage)에 `toggle` 옵션을 추가하였으며, 오래된 경로 및 파일명 언급을 정리하였다.
* **`tests/`**: 3개의 파일과 가짜 `picamera2` 모듈(`tests/fakes/`), 23개의 테스트 코드 및 `pytest.ini`를 추가 및 수정하였다.