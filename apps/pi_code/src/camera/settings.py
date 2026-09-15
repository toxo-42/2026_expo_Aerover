"""카메라 설정값. '무엇을 어떻게 찍을지'만 담고 카메라 객체는 모른다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class StreamCameraSettings:
    """TCP JPEG 스트림용 (원본 cam_server.py)."""
    size: tuple[int, int] = (1280, 960)
    # raw 를 명시하지 않으면 libcamera 가 16:9 센서모드(2028x1080)를 골라
    # 위아래 440px 씩(센서 높이의 29%) 버린다. 2028x1520 은 풀센서 4:3 비닝
    # 모드라 ScalerCrop 이 (2,0,4052,3040) — 화각을 다 쓴다. (2026-09-09 실측)
    raw_size: tuple[int, int] = (2028, 1520)
    hflip: bool = True      # hflip + vflip = 180도 회전
    vflip: bool = True


@dataclass(frozen=True)
class RecordCameraSettings:
    """H.264 녹화용 (원본 daemons/daemon.py). 원본대로 회전 없음."""
    size: tuple[int, int] = (1920, 1080)
    fps: int = 30
    buffer_count: int = 6


@dataclass(frozen=True)
class WebCameraSettings:
    """Flask 웹용 (programs/web.py). 카메라 하나에 main(H.264 녹화) + lores(MJPEG 미리보기).

    카메라는 한 프로세스만 열 수 있어서 녹화와 미리보기를 한 설정에 같이 담는다.
    """
    size: tuple[int, int] = (1920, 1080)
    preview_size: tuple[int, int] = (640, 360)    # main 과 같은 16:9 — 다르면 찌그러진다
    fps: int = 30
    buffer_count: int = 6
    hflip: bool = True      # 현재 장착 방향(cam_server.py 기준 180도)에 맞춤
    vflip: bool = True


@dataclass(frozen=True)
class StillCameraSettings:
    """인터벌 정지영상용 (원본 imagesend.py)."""
    size: tuple[int, int] = (1920, 1080)
    format: str = "RGB888"
    warmup_sec: float = 2.0     # 센서 안정화 (자동노출/화이트밸런스 수렴)


def exposure_controls_from_env(env: Mapping[str, str] = os.environ) -> dict:
    """노출 수동 고정. 환경변수 EXP(마이크로초)를 주면 AE 를 끄고 그 값으로 박는다.

    **주지 않으면 빈 dict → 기존과 완전히 동일하게 자동 노출로 동작한다.**
    걸으면서/날면서 찍으면 AE 가 잡는 10ms 로는 모션 블러가 남는다 (2026-09-07 실측).
    """
    exp = env.get("EXP")
    if not exp:
        return {}
    return {"AeEnable": False,
            "ExposureTime": int(exp),
            "AnalogueGain": float(env.get("GAIN", 8.0))}
