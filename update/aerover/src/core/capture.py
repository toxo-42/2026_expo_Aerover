"""수집 회차 — 폴더를 만들고 간격마다 받은 JPEG 원본을 저장한다.

Qt 를 모른다. 화면(`pages/status.py`)은 프레임이 올 때마다 `offer()` 만 부른다.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

SESSION_STAMP = "%Y%m%d_%H%M%S"     # 회차 폴더명. 매핑 페이지가 이름순으로 정렬한다
FRAME_NAME = "{index:03d}.jpg"      # 회차 안 파일명. 순서가 곧 촬영 순서다


def new_session_dir(root: Path, now: datetime | None = None) -> Path:
    """회차마다 폴더를 나눈다. ODM 좌표계가 회차마다 달라 섞으면 안 된다."""
    stamp = (now or datetime.now()).strftime(SESSION_STAMP)
    path = root / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


class CaptureSession:
    def __init__(self, root: Path, interval: float, target: int) -> None:
        self.dir = new_session_dir(root)
        self.interval = interval
        self.target = target        # 0 이면 정지할 때까지 계속 저장한다
        self.saved = 0
        self._last = 0.0

    def offer(self, raw: bytes, now: float) -> bool:
        """간격이 지났으면 저장하고 True. 첫 프레임은 바로 저장한다."""
        if now - self._last < self.interval:
            return False
        # 받은 JPEG 원본 그대로 — 재인코딩하지 않는다
        (self.dir / FRAME_NAME.format(index=self.saved)).write_bytes(raw)
        self.saved += 1
        self._last = now
        return True

    @property
    def done(self) -> bool:
        return bool(self.target) and self.saved >= self.target
