#!/usr/bin/env python3
"""파이 드론 카메라 실행 진입점. 저장소 루트(파이: ~/drone)에서 실행한다.

    PY=~/drone/droneenv/bin/python
    $PY app.py stream                 # 지상국 UDP 링크 (MAVLink 14550 · RTP → 5004)
    EXP=2500 GAIN=8 $PY app.py stream # 노출 고정
    $PY app.py tcp                    # 옛 TCP 스트림 (포트 5001)
    sudo $PY app.py record            # 녹화 데몬 (버튼·LED·제어 소켓)
    $PY app.py cam [start|stop|status|toggle]
    $PY app.py web                    # 웹 미리보기 + 녹화 (flask 필요)
    $PY app.py udp                    # 인터벌 UDP 전송
"""
import importlib
import sys

# 명령 → src/programs/ 모듈. 고른 것만 import 한다 —
# cam 은 picamera2 없이, stream 은 flask 없이 떠야 하기 때문이다.
PROGRAMS = {
    "stream": "지상국 UDP 링크 — MAVLink 제어 + RTP/JPEG 영상",
    "tcp":    "옛 TCP JPEG 스트림      (원본 cam_server.py)",
    "record": "녹화 데몬               (원본 daemons/daemon.py)",
    "cam":    "녹화 데몬 제어          (원본 cam 스크립트)",
    "web":    "웹 미리보기 + 녹화      (새로 작성)",
    "udp":    "인터벌 UDP 전송         (원본 imagesend.py)",
}


def usage() -> str:
    lines = ["사용법: python app.py <명령> [인자]", ""]
    lines += [f"  {name:7} {desc}" for name, desc in PROGRAMS.items()]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in PROGRAMS:
        print(usage())
        return 2

    name, rest = argv[0], argv[1:]
    if name == "cam":
        # 뒤 인자를 그대로 넘긴다 — 원본 cam 과 출력·종료코드가 같다
        from src.control.cli import main as cam_main
        return cam_main(rest)

    importlib.import_module(f"src.programs.{name}").main()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
