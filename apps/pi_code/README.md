# pi_code — 라즈베리파이 드론 카메라

### 참고사항
- pi 안에 있는 코드를 빼와 내 개인 컴퓨터에서 solid 형식에 맞춰서 정리하게 끔 시킨뒤 다시 pi 안에 넣어놓는 식으로 작업을 진행함
- 다시 넣어놓고 우리 프로그램 돌려보니 정상적으로 동작되는것 확인 하였음. (2026-09-13 정리분은 아직 파이 실기 확인 전 — 아래 "검증")

## 실행

파이에서, 이 폴더(= `~/drone`)에서:

```bash
# venv 가 없으면 먼저 (picamera2 등 apt 패키지가 보이도록 --system-site-packages). 자세한 건 requirements.txt 머리말
python3 -m venv --system-site-packages droneenv
droneenv/bin/pip install -r requirements.txt

PY=~/drone/droneenv/bin/python      # 시스템 패키지(picamera2)가 보이는 venv

$PY app.py stream                   # 지상국 UDP 링크 — MAVLink(14550) 로 제어받고 RTP/JPEG 를 지상국 5004 로
EXP=2500 GAIN=8 $PY app.py stream   # 노출 고정 (실내 기준값 — 야외는 다시 잴 것)
DRONECAM_FC_SERIAL=/dev/serial0 $PY app.py stream   # FC 의 MAVLink 텔레메트리도 중계 (배선 후)
$PY app.py tcp                      # 옛 TCP 스트림 (포트 5001) — 지상국은 AEROVER_LINK=tcp
$PY app.py record                   # 녹화 데몬 — 소켓이 ~/drone/run/ 에 생겨 sudo 가 필요 없다
$PY app.py cam status               # 녹화 데몬 제어: start | stop | status | toggle
DRONECAM_DEST_IP=192.168.137.1 $PY app.py udp   # 인터벌 UDP 전송

sudo apt install python3-flask      # web 만 필요 (파이에 미설치)
$PY app.py web                      # http://<파이IP>:8000/

python -m pytest                    # 테스트 — 파이 밖에서도 돈다 (가짜 picamera2)
```

**카메라는 한 번에 한 프로세스만 연다.** 새 프로그램을 시험하기 전에 서비스를 멈출 것:
`sudo systemctl stop aerover-cam` (끝나면 `start`).
`pgrep -f cam_server.py` 는 자기 명령줄에도 매치되니 쓰지 말 것.

옛 시스템 소켓 자리(`/run/dronecam/control.sock`, 파이에 설치된 원본 `cam` 스크립트가 보는 곳)를 그대로 쓰려면
`DRONECAM_SOCK=/run/dronecam/control.sock` 을 주고 `sudo -E` 로 띄운다.

## 설정

환경마다 바꾸는 값(포트·경로·비트레이트·GPIO 핀·전송 대상)은 **전부 `src/config.py` 한 곳**에 있다.
카메라 해상도·회전·센서 모드처럼 '무엇을 어떻게 찍을지'는 `src/camera/settings.py` 에 있다.

- **경로는 전부 저장소 루트 기준이다.** 녹화본은 `rec/`, 제어 소켓은 `run/control.sock` — 둘 다 `app.py` 옆이다. 절대경로는 코드 어디에도 없다.
- 자리마다 다른 값은 환경변수로 덮어쓴다: `DRONECAM_MAVLINK_PORT` · `DRONECAM_RTP_PORT` · `DRONECAM_FC_SERIAL` · `DRONECAM_FC_BAUD` · `DRONECAM_STREAM_PORT` · `DRONECAM_SOCK` · `DRONECAM_DEST_IP` · `DRONECAM_DEST_PORT` · `DRONECAM_WEB_HOST` · `DRONECAM_WEB_PORT` · `EXP` · `GAIN`. MAVLink·RTP 포트는 aerover 설정과 같아야 한다.

## 구조

```
~/drone/  (= pi_code/)
├── app.py                 실행 진입점 — python app.py <명령>
├── README.md
├── pytest.ini
│
├── src/                   분리본
│   ├── config.py          설정 한 곳 — 경로는 ROOT 기준, 장비는 환경변수
│   ├── log.py             [HH:MM:SS] 메시지 출력
│   ├── programs/          조립(wiring) — 명령 하나에 파일 하나
│   │   ├── stream.py      (새로 작성 — UDP: MAVLink 제어 + RTP/JPEG 영상)
│   │   ├── tcp.py         ← cam_server.py (옛 TCP 스트림)
│   │   ├── record.py      ← daemons/daemon.py
│   │   ├── web.py         (새로 작성)
│   │   └── udp.py         ← imagesend.py
│   ├── camera/            settings · factory
│   ├── stream/            rtpjpeg(aerover 와 동일) · rtp_output · rtp_session · framing · socket_output · tcp_server · mjpeg · jpeg · udp_sender
│   ├── recorder/          recorder · raw_output · report
│   ├── hardware/          gpio · led · button
│   ├── control/           camera_node(MAVLink) · fc_bridge · protocol · unix_server · cli   (cli = app.py cam)
│   └── web/               app · templates/index.html
│
├── tests/                 pytest. fakes/ 에 가짜 picamera2 · libcamera
│
├── cam_server.py          ┐
├── cam_server.py.*bak     │
├── daemons/daemon.py      │ 원본 — 제자리 그대로, 수정 금지 (절대경로가 남아 있지만 보관본이다)
├── imagesend.py           │
├── imagesend_tcp.py       │
├── stream_send.py         │
├── image_interval.py      │
├── secound.py             ┘
│
├── requirements.txt       pip 로 까는 것 (opencv) + venv 만드는 법. droneenv/ 는 저장소에 없다 — 파이에서 만든다
├── rec/                   녹화본 flight_*.h264 / .pts / .log
├── run/                   실행 중에만 있는 것 — 제어 소켓 (데몬이 만든다)
├── captures/              image_interval.py 저장 폴더
├── videos/                secound.py 저장 폴더
└── test.jpg
```

의존 방향은 `app.py → programs → 부품 패키지` 한쪽이다.
**부품 패키지끼리는 조립하지 않고, 조립은 `programs/` 에서만 한다.** 그래서 `programs/` 파일 하나를 읽으면 그 프로그램이 어떤 부품으로 이뤄졌는지 보인다.

`app.py` 는 고른 명령의 모듈만 import 한다. 프로그램마다 필요한 라이브러리가 달라서다 —
`cam` 은 picamera2 없이, `stream` 은 flask 없이 떠야 한다.

부품은 장비를 직접 만들지 않고 주입받는다 — `TcpStreamServer(open_camera, make_encoder)`,
`Recorder(camera, make_encoder, make_output)`, `IntervalSender(capture, encode, sender)`,
`ButtonControl(button, target, led)`. 그래서 `tests/fakes/` 의 가짜 카메라로 조립까지 검증한다.

## 원본 목록

| 파일 | 최종 수정 | 상태 | 분리본 |
|---|---|---|---|
| `cam_server.py` | 09-09 |  (`aerover-cam.service`, enabled) | `app.py tcp` (UDP 새 방식은 `app.py stream`) |
| `daemons/daemon.py` | 08-01 | `dronecam.service` — **disabled** | `app.py record` |
| 파이 시스템 경로의 `cam` | 07-31 | 위 데몬 제어 CLI (**이 폴더에 없다**) | `app.py cam` |
| `imagesend.py` | 07-08 | UDP 청크 전송 실험 | `app.py udp` |
| `imagesend_tcp.py` | 07-08 | TCP 30장 전송 실험 | 보관만 |
| `stream_send.py` | 07-10 | 파이→노트북 접속형 스트림 (cam_server 이전 방식) | 보관만 |
| `image_interval.py` | 07-03 | 인터벌 촬영 → 로컬 JPG | 보관만 |
| `secound.py` | 07-03 | 30초 mp4 녹화 | 보관만 |
| `cam_server.py.bak` / `.20260907bak` / `.20260909bak` | | 수정 전 백업 | 보관만 |

systemd 유닛(`aerover-cam.service`, `dronecam.service`)도 파이 시스템 경로에 있고 이 폴더에는 없다.

## 부품

| 모듈 | 책임 | 외부 의존 |
|---|---|---|
| `config.py` | 포트·경로·녹화 품질·GPIO 핀·전송 대상 (ROOT 기준, 환경변수) | — |
| `log.py` | `[HH:MM:SS] 메시지` 출력 | — |
| `camera/settings.py` | 용도별 카메라 설정값(dataclass) + `EXP`/`GAIN` → 노출 controls | — |
| `camera/factory.py` | 설정 → 구성된 `Picamera2`. 구성 실패 시 카메라 해제 | picamera2 |
| `stream/rtpjpeg.py` | RTP/JPEG(RFC 2435) 패킷화·재조립 — 순수 바이트 로직, aerover 와 같은 파일 | — |
| `stream/rtp_output.py` | 인코더 출력 → RTP 패킷 → UDP. 실패는 세기만 한다 | — |
| `stream/rtp_session.py` | 지상국 주소 하나에 카메라 열고 보내기, 멈추면 놓기 | picamera2 |
| `control/camera_node.py` | MAVLink 카메라 노드 — HEARTBEAT · VIDEO_START/STOP 처리 · ACK · 지상국 HEARTBEAT 타임아웃 | pymavlink |
| `control/fc_bridge.py` | FC 의 MAVLink UART → 메시지 단위로 지상국에 중계 | pymavlink · pyserial |
| `stream/framing.py` | TCP 길이 헤더, UDP 청크 분할 — 순수 함수 | — |
| `stream/socket_output.py` | 인코더 출력 → TCP 소켓 어댑터. 끊기면 `alive` Event 내림, 그 뒤엔 보내지 않음 | — |
| `stream/tcp_server.py` | 접속 1개 수명 관리 (카메라 열기·닫기). 송신 타임아웃 + keepalive 로 Wi-Fi 끊김에서 회복 | picamera2 |
| `stream/mjpeg.py` | 최신 JPEG 1장을 여러 웹 클라이언트에 배포 + multipart 본문 | — |
| `stream/jpeg.py` | BGR 배열 → JPEG 바이트 | PIL |
| `stream/udp_sender.py` | `UdpChunkSender`(한 장 전송), `IntervalSender`(주기 루프, 출력은 `on_sent` 콜백) | — |
| `recorder/recorder.py` | 녹화 상태 머신: start/stop/toggle/status, 락으로 스레드 안전 | — |
| `recorder/raw_output.py` | H.264 + pts 무버퍼 기록, 주기 fsync, 프레임 결손 감지 | picamera2 |
| `recorder/report.py` | 녹화 통계 계산 + `.log` 파일 작성 | — |
| `hardware/gpio.py` | gpiozero 유무 판별, LED·Button 생성 | gpiozero(선택) |
| `hardware/led.py` | 상태 LED 모드 (idle/rec/warn) | — |
| `hardware/button.py` | 짧게=토글, 길게=종료 판정 | — |
| `control/protocol.py` | 명령 문자열 → 처리 함수 → JSON 한 줄 | — |
| `control/unix_server.py` | 유닉스 소켓 accept 루프 + 주기 `on_tick` | — |
| `control/cli.py` | 소켓 명령 전송 + 응답 출력 (`app.py cam`) | — |
| `web/app.py` | Flask 라우트 (`/`, `/stream.mjpg`, `/api/status`, `POST /api/record/<start\|stop\|toggle>`) | flask |

## 프로그램별 동작

- **stream** — UDP. MAVLink 포트 14550 에서 지상국 HEARTBEAT 와 `VIDEO_START_STREAMING` 을 받으면 카메라를 열어 **그 지상국 주소의 5004** 로 RTP/JPEG 를 보낸다. `VIDEO_STOP_STREAMING` 이 오거나 지상국 HEARTBEAT 가 3초 끊기면 카메라를 놓는다. 1280x960, raw 2028x1520(풀센서 4:3 화각), 180도 회전. `DRONECAM_FC_SERIAL` 을 주면 FC 의 MAVLink 텔레메트리를 같은 소켓으로 중계한다. 받는 쪽은 `aerover/src/core/link.py` 의 `RtpLinkWorker`.
- **tcp** — 옛 방식, 포트 5001. 접속마다 카메라를 열어 `[4바이트 길이][JPEG]` 를 보내고, 끊기면 카메라 해제. 5초 안에 못 보내면 끊긴 것으로 보고 다음 접속을 받는다. 지상국은 `AEROVER_LINK=tcp`.
- **record** — 카메라를 켜둔 채 대기하다 버튼(GPIO17)·`cam` 명령으로 H.264 1080p30 녹화. LED(GPIO27): 대기 2초 깜빡 / 녹화 점등 / 경고 빠른 점멸. 제어 소켓 `run/control.sock` (`DRONECAM_SOCK` 으로 변경). 길게 눌러 종료는 `ButtonSettings.enable_hold_exit` 로 켜며 기본은 꺼짐(원본과 같음).
- **web** — 카메라 하나에 인코더 두 개: main 1920x1080 → H.264 녹화, lores 640x360 → MJPEG 미리보기. 녹화 파일은 record 와 같은 `rec/flight_*` 형식.
- **udp** — 2초마다 1920x1080 정지영상 → JPEG(q80) → 60KB 청크(`!IHH` 헤더)로 UDP 9999 전송.

원본과 다른 점:
- `cam_server.py` 의 `flag = [True]` 리스트 → `threading.Event`. 폴링 주기(0.1초) 같음.
- 카메라 구성(configure) 실패 시 factory 가 카메라를 닫고 예외를 다시 던진다. 원본 `cam_server.py` 도 finally 에서 닫았으니 결과는 같다.
- `cam` CLI 가 소켓을 `with` 로 닫는다 (원본은 프로세스 종료에 맡김).
- 녹화 저장 위치가 절대경로 대신 **`app.py` 옆 `rec/`** 다. `~/drone` 에 넣으면 같은 곳이다. 제어 소켓도 `app.py` 옆 `run/` 이라 `sudo` 없이 뜬다.
- web 녹화는 **180도 회전이 켜져 있다.** 원본 데몬 녹화는 회전이 없었다(`RecordCameraSettings` 는 원본대로 회전 없음). 장착 방향을 확인할 것.
- 스트림 클라이언트 소켓에 송신 타임아웃(5초)과 keepalive 가 걸린다. 원본은 없었다.

## 검증

**2026-09-10 (맥, 재구성 전 배치)** — 원본 13개 SHA-256 파이와 일치, 부품 동작 45개(가짜 카메라·출력), `cam` CLI ↔ 원본 `cam` 10개 명령 출력·종료코드 동일, Flask 라우트 11개.

**2026-09-12 (맥, 재구성 후, Python 3.13 + 가짜 picamera2)** — 24개 통과:
- 원본·데이터 1970개 파일 해시 재구성 전과 동일
- `app.py` 사용법 / `app.py cam status`·`--help` 가 picamera2 없이 실행
- `programs/` 4개 import, 패키지 → programs 역참조 없음
- stream: `[!I][JPEG]` 프레임 수신, 끊으면 카메라 해제, 재접속 2회
- record ↔ cam: status·start·중복 start·stop·잘못된 명령의 출력과 종료코드, `rec/` 에 `.h264/.pts/.log` 생성과 `.log` 형식, SIGTERM 종료 시 소켓 정리

**2026-09-13 (윈도우, 절대경로 제거 · 정리 후, Python 3.12 + `tests/fakes/`)** — `python -m pytest` 23개 통과, 1개 건너뜀(유닉스 소켓은 윈도우에 없음). 원본 파일은 손대지 않았다.
- stream: 소켓 한 쌍으로 프레임 3장 수신 → 끊으면 카메라 `close`, 구성 실패 시 소켓 닫힘
- record: start/중복/stop/toggle, `.h264/.pts/.log`, 결손 감지
- control: dispatch · cli 출력 · 종료코드
- hardware: LED 모드 전환, 버튼 짧게/길게
- web: `/`, `/api/status`, `/api/record/*`, `/stream.mjpg` multipart

**2026-09-13 (윈도우, UDP 전환 후)** — `python -m pytest` 36개 통과, 1개 건너뜀.
- rtpjpeg: PIL·OpenCV JPEG 왕복(픽셀 동일) · 4:2:2 · 재시작 마커 · 유실 · 순서 뒤바뀜 · 비표준 허프만 거부 (aerover 쪽 테스트, 파일 동일 대조)
- rtp_output → 소켓 → 재조립, rtp_session 카메라 수명, camera_node 핸드셰이크·ACK·타임아웃·실패 보고·중계, fc_bridge 조각난 입력

**2026-09-15 (파이 실기, Raspberry Pi OS · Python 3.13.5 · imx477)** — stream 을 실제 카메라로 지상국까지 확인, FC → 파이 MAVLink 수신 확인. pytest 는 파이에서 돌리지 않았다.
- 배포: 맥 `pi_code/` → 파이 `~/drone/` rsync (`__pycache__`·`*bak`·`.DS_Store` 제외). `cam_server.py`·`daemons/daemon.py` 는 파이 원본과 동일(cmp)
- venv(`droneenv`, `--system-site-packages`): `import cv2, pymavlink, serial, picamera2` 성공 — opencv-python 5.0.0.93 · pymavlink 2.4.49 · pyserial 3.5, picamera2·libcamera 는 apt
- `app.py stream`: UDP 14550 대기 → 지상국(맥 192.168.137.78) HEARTBEAT 수신 → 카메라 구성 `1280x960-XBGR8888` + raw `2028x1520-SRGGB12` → **지상국 화면에 영상 나옴** (육안)
- 지상국 "파이 응답 없음" 원인: 서비스가 옛 `cam_server.py`(TCP 5001)만 띄워 14550 을 듣는 프로세스가 없었다 (UDP 라 거부 없이 5초 타임아웃)
- systemd `aerover-cam.service`: `ExecStart` 를 `app.py stream` 으로 바꾸고 `WorkingDirectory=/home/drone/drone` 추가 (원래 `cam_server.py` 줄은 유닛 안에 주석으로 남김). 재시작 후 active · 14550 대기 · 로그 `MAVLink 대기 중` 확인 → 지상국 재연결 후 영상 나옴 (육안)
- 옛 파이 코드·녹화(`rec/` 566M)는 `~/drone_backup` 에 보관 중
- 재부팅 뒤 `aerover-cam` 자동 시작 (active · 14550 대기)
- **FC(SpeedyBee F405 V4, INAV) → 파이 MAVLink 수신 됨** — FC T → GPIO15(10번 핀) · GND, 파이 시리얼 콘솔 끔, `/dev/serial0` 57600 에서 HEARTBEAT · ATTITUDE · SYS_STATUS · VFR_HUD 등 수신 (INAV 기본 전송률 1~2Hz, GPS_RAW_INT 는 안 옴 — GPS 배선 없음). MSP 는 코드 없음
- **FC 텔레메트리 지상국 중계 됨** (2026-09-20) — 서비스에 `Environment="DRONECAM_FC_SERIAL=/dev/serial0"` 추가(원본은 `aerover-cam.service.20260920bak`), 로그 `FC 중계 /dev/serial0` 확인. 지상국 계기판에 배터리(22.86V·56%)·모드·자세·기압고도 들어옴
- RC_CHANNELS 는 조종 링크가 끊겨 있으면 페일세이프 기본값(전 채널 고정 · `rssi 0` · `SYS_STATUS` 의 RC_RECEIVER health=False)이 온다. 살아 있으면 `rssi 254` · 채널이 스틱을 따라 움직인다

**아직 안 한 것 — 파이 실기 검증.** 아래는 파이에서 아직 돌려보지 않았다.
- Wi-Fi 에서의 프레임 유실률(패킷 하나가 빠지면 그 프레임을 버린다), 지상국 HEARTBEAT 타임아웃 뒤 재접속, `VIDEO_STOP_STREAMING`
- 파이에서 `python -m pytest`
- `camera/factory.py` 의 web 구성 (main + lores 동시 구성). stream 구성은 위에서 확인
- 실제 H.264 기록, 실제 TCP 스트림을 aerover 로 수신
- web 에서 H.264 + MJPEG 하드웨어 인코더 동시 사용
- 실제 버튼·LED
- 제어 소켓이 `~/drone/run/` 으로 옮겨진 뒤 `app.py record` ↔ `app.py cam`
