"""3D 매핑 — UI 스레드 밖에서 도는 워커. ODM 워커는 `core/odm.py` 에 있다."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.core import cropper
from src.core.imgcheck import check_folder


class CheckWorker(QThread):
    """①입력 검사. 446장에 약 46초가 걸려 UI 스레드에서 돌릴 수 없다.

    `core.imgcheck` 는 Qt 를 모른다 — 콜백만 받는다. 그래서 CLI 로 따로 검증할 수 있다.
    """

    progress = Signal(int, int)     # (done, total)
    finished_ok = Signal(object)    # CheckReport

    def __init__(self, folder: Path, parent=None) -> None:
        super().__init__(parent)
        self.folder = folder
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        report = check_folder(self.folder,
                              progress=lambda d, t: self.progress.emit(d, t),
                              cancel=lambda: self._cancel)
        if not self._cancel:
            self.finished_ok.emit(report)


class CropWorker(QThread):
    """메시 크롭 + GLB 내보내기. 203k 면 기준 약 3.6초 — UI 를 얼리기엔 길다."""

    finished_ok = Signal(object)      # cropper.CropResult
    failed = Signal(str)

    def __init__(self, scene, out: Path, lo, hi, plane, parent=None) -> None:
        super().__init__(parent)
        self.scene, self.out, self.lo, self.hi, self.plane = scene, out, lo, hi, plane

    def cancel(self) -> None:
        """`shutdown()` 규약을 맞춘다. 3.6초짜리라 중간에 끊지 않고 끝나길 기다린다."""

    def run(self) -> None:
        try:
            self.finished_ok.emit(cropper.crop(self.scene, self.out, self.lo, self.hi, self.plane))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")
