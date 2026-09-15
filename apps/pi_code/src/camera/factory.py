"""설정값 → 구성까지 끝난 Picamera2. 시작(start)은 호출한 쪽이 정한다."""
from __future__ import annotations

from typing import Callable

from libcamera import Transform
from picamera2 import Picamera2

from src.camera.settings import (RecordCameraSettings, StillCameraSettings, StreamCameraSettings,
                             WebCameraSettings)


def _open(build_config: Callable[[Picamera2], dict]) -> Picamera2:
    picam2 = Picamera2()
    try:
        picam2.configure(build_config(picam2))
    except Exception:
        picam2.close()      # 구성 실패 시에도 카메라를 놓아야 다음 접속이 열 수 있다
        raise
    return picam2


def open_stream_camera(s: StreamCameraSettings, controls: dict) -> Picamera2:
    return _open(lambda cam: cam.create_video_configuration(
        main={"size": s.size},
        raw={"size": s.raw_size},
        transform=Transform(hflip=int(s.hflip), vflip=int(s.vflip)),
        controls=controls,
    ))


def open_record_camera(s: RecordCameraSettings) -> Picamera2:
    return _open(lambda cam: cam.create_video_configuration(
        main={"size": s.size},
        controls={"FrameRate": s.fps},
        buffer_count=s.buffer_count,
    ))


def open_web_camera(s: WebCameraSettings, controls: dict) -> Picamera2:
    # lores 는 Pi 4 에서 YUV420 만 된다. V4L2 MJPEG 인코더가 YUV420 입력을 받는다 (0.3.36 확인)
    return _open(lambda cam: cam.create_video_configuration(
        main={"size": s.size},
        lores={"size": s.preview_size, "format": "YUV420"},
        transform=Transform(hflip=int(s.hflip), vflip=int(s.vflip)),
        controls={"FrameRate": s.fps, **controls},
        buffer_count=s.buffer_count,
    ))


def open_still_camera(s: StillCameraSettings) -> Picamera2:
    return _open(lambda cam: cam.create_still_configuration(
        main={"size": s.size, "format": s.format},
    ))
