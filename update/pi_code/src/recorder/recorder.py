from __future__ import annotations

import datetime
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from src.log import log
from src.recorder.report import RecordingStats, write_log


class RawOutput(Protocol):
    frames: int
    gaps: list[tuple[int, float]]

    def close(self) -> None: ...


def _sync_disk() -> None:
    """전원이 갑자기 끊겨도 닫은 파일이 남도록 커널 버퍼를 비운다. (리눅스 전용 — 없는 OS 는 건너뛴다)"""
    sync = getattr(os, "sync", None)
    if sync is not None:
        sync()


@dataclass(frozen=True)
class RecordSettings:
    rec_dir: str
    fps: int
    gap_ms: int         # 이 간격(ms)보다 벌어지면 프레임 결손으로 판정
    prefix: str = "flight"


class Recorder:
    """녹화 상태 머신 (대기 ↔ 녹화).

    카메라는 이미 start() 된 것을 받아 인코더만 붙였다 뗀다 → 트리거 즉시 반응.
    인코더·출력 생성은 주입받으므로 버튼·소켓·웹 어느 쪽에서 불러도 같은 동작을 한다.
    """

    def __init__(self, camera,
                 settings: RecordSettings,
                 make_encoder: Callable[[], object],
                 make_output: Callable[[str, str], RawOutput]) -> None:
        self.camera = camera
        self.settings = settings
        self._make_encoder = make_encoder
        self._make_output = make_output

        self.lock = threading.RLock()   # toggle()이 start/stop을 재진입 호출
        self.encoder = None
        self.output: RawOutput | None = None
        self.base: str | None = None
        self.t0: float | None = None

        os.makedirs(settings.rec_dir, exist_ok=True)

    @property
    def recording(self) -> bool:
        return self.encoder is not None

    def gap_count(self) -> int:
        out = self.output
        return len(out.gaps) if out else 0

    def _stats(self) -> RecordingStats:
        return RecordingStats.measure(file=f"{self.base}.h264",
                                      elapsed=time.time() - self.t0,
                                      frames=self.output.frames,
                                      fps=self.settings.fps,
                                      gaps=len(self.output.gaps))

    def start(self) -> dict:
        with self.lock:
            if self.encoder:
                return {"ok": False, "msg": "이미 녹화 중",
                        "file": f"{self.base}.h264"}

            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.base = os.path.join(self.settings.rec_dir, f"{self.settings.prefix}_{stamp}")

            self.output = self._make_output(f"{self.base}.h264", f"{self.base}.pts")
            self.encoder = self._make_encoder()
            self.camera.start_encoder(self.encoder, self.output)
            self.t0 = time.time()

            log(f"녹화 시작 → {self.base}.h264")
            return {"ok": True, "msg": "녹화 시작", "file": f"{self.base}.h264"}

    def stop(self) -> dict:
        with self.lock:
            if not self.encoder:
                return {"ok": False, "msg": "녹화 중이 아님"}

            self.camera.stop_encoder(self.encoder)
            self.output.close()
            _sync_disk()

            stats = self._stats()
            res = {"ok": True, "msg": "녹화 종료", **stats.as_dict()}
            write_log(f"{self.base}.log", stats, self.output.gaps, self.settings.gap_ms)

            self.encoder = self.output = None
            log(f"녹화 종료 {res}")
            return res

    def toggle(self) -> dict:
        with self.lock:
            return self.stop() if self.encoder else self.start()

    def status(self) -> dict:
        with self.lock:
            if not self.encoder:
                return {"ok": True, "recording": False}
            return {"ok": True, "recording": True, **self._stats().as_dict()}
