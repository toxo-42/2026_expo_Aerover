from __future__ import annotations


class Output:
    def __init__(self) -> None:
        self.recording = False

    def outputframe(self, frame, keyframe=True, timestamp=None, *a, **kw) -> None:
        pass

    def start(self) -> None:
        self.recording = True

    def stop(self) -> None:
        self.recording = False

    def close(self) -> None:
        pass


class FileOutput(Output):
    """진짜와 같은 규약 — 파일 객체(write 가 있는 것)를 받아 프레임마다 write 한다."""

    def __init__(self, file=None, pts=None, split=None) -> None:
        super().__init__()
        self._file = file

    def outputframe(self, frame, keyframe=True, timestamp=None, *a, **kw) -> None:
        self._file.write(frame)
