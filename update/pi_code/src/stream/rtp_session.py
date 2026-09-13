"""RTP 스트림 세션 — 지상국 주소 하나에 카메라를 열어 보내고, 멈추면 카메라를 놓는다.

카메라를 어떻게 여는지·어떤 인코더·출력을 쓰는지는 주입받는다. 이 클래스는 **카메라의 수명**만 책임진다.
"""
from __future__ import annotations

import io
from typing import Callable

from picamera2 import Picamera2
from picamera2.encoders import Encoder
from picamera2.outputs import FileOutput

Address = tuple[str, int]


class RtpStreamSession:
    def __init__(self, open_camera: Callable[[], Picamera2],
                 make_encoder: Callable[[], Encoder],
                 make_output: Callable[[Address], io.BufferedIOBase]) -> None:
        self._open_camera = open_camera
        self._make_encoder = make_encoder
        self._make_output = make_output
        self._camera: Picamera2 | None = None
        self._dest: Address | None = None

    @property
    def streaming(self) -> bool:
        return self._camera is not None

    @property
    def dest(self) -> Address | None:
        return self._dest

    def start(self, dest: Address) -> None:
        """이미 같은 곳으로 보내고 있으면 그대로 둔다. 다른 곳이면 갈아탄다. 카메라 오류는 그대로 던진다."""
        if self._camera is not None and self._dest == dest:
            return
        self.stop()
        camera = self._open_camera()
        try:
            camera.start_recording(self._make_encoder(), FileOutput(self._make_output(dest)))
        except Exception:
            camera.close()
            raise
        self._camera, self._dest = camera, dest

    def stop(self) -> None:
        camera, self._camera, self._dest = self._camera, None, None
        if camera is None:
            return
        try:
            camera.stop_recording()
        except Exception:
            pass
        camera.close()
