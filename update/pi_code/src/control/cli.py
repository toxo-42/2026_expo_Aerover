"""cam 명령 (원본은 파이 시스템 경로의 `cam` 스크립트). 데몬 소켓에 명령을 보내고 응답을 사람이 읽게 출력한다."""
from __future__ import annotations

import json
import socket

from src.config import SOCK_PATH


def send_command(cmd: str, path: str = SOCK_PATH, timeout: float = 5.0) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall(cmd.encode())
        return json.loads(s.recv(4096).decode())


def describe(r: dict) -> list[str]:
    if r.get("recording") is False:
        return ["대기 중 (녹화 안 함)"]
    lines = [r.get("msg", "녹화 중")]
    if "file" in r:
        lines.append(f"  파일   : {r['file']}")
    if "frames" in r:
        lines.append(f"  경과   : {r['seconds']} s")
        lines.append(f"  프레임 : {r['frames']} / 기대 {r['expected']}")
        lines.append(f"  결손   : {r['gaps']} 건")
    return lines


def main(argv: list[str], path: str = SOCK_PATH) -> int:
    cmd = (argv[0] if argv else "status").lower()

    if cmd in ("-h", "--help", "help"):
        print("사용법: cam [start|stop|status|toggle]")
        return 0

    try:
        r = send_command(cmd, path)
    except FileNotFoundError:
        print(f"소켓이 없습니다 ({path})")
        print("데몬이 안 떠 있습니다 →  systemctl status dronecam.service")
        return 1
    except PermissionError:
        print("소켓 접근 권한 없음 →  groups  로 video 그룹 확인")
        return 1
    except Exception as e:
        print(f"데몬 연결 실패: {e}")
        return 1

    for line in describe(r):
        print(line)
    return 0 if r.get("ok") else 1
