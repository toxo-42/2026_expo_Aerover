"""가짜 picamera2 — 호출 순서만 기록한다. 테스트가 `Picamera2.instances` 로 들여다본다."""
from __future__ import annotations

import numpy as np


class Picamera2:
    instances: list["Picamera2"] = []

    def __init__(self) -> None:
        self.config = None
        self.started = False
        self.closed = False
        self.recording = None                   # (encoder, output) — start_recording
        self.encoders: list[tuple] = []         # [(encoder, output, name)] — start_encoder
        Picamera2.instances.append(self)

    # 구성
    def create_video_configuration(self, **kw) -> dict:
        return {"kind": "video", **kw}

    def create_still_configuration(self, **kw) -> dict:
        return {"kind": "still", **kw}

    def configure(self, config: dict) -> None:
        if config.get("fail"):
            raise RuntimeError("configure failed")
        self.config = config

    # 수명
    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True

    # 인코더
    def start_recording(self, encoder, output) -> None:
        self.started = True
        self.recording = (encoder, output)
        encoder.attach(output)

    def stop_recording(self) -> None:
        if self.recording:
            self.recording[0].detach()
        self.recording = None
        self.started = False

    def start_encoder(self, encoder, output, name=None) -> None:
        self.encoders.append((encoder, output, name))
        encoder.attach(output)

    def stop_encoder(self, encoder) -> None:
        encoder.detach()
        self.encoders = [e for e in self.encoders if e[0] is not encoder]

    def capture_array(self) -> np.ndarray:
        frame = np.zeros((4, 6, 3), dtype=np.uint8)
        frame[..., 0] = 255                     # B 채널만 켠다 — BGR→RGB 뒤집기 검증용
        return frame
