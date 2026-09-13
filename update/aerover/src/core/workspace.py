"""회차 산출물 폴더 — `3D_model/<입력폴더명>/`.

회차마다 좌표계가 달라 산출물을 섞으면 안 된다 (콘티 2절). 그래서 입력 폴더마다
하위 폴더를 나눈다. 폴더명이 겹칠 수 있어(`sub` 같은 흔한 이름) `source.txt` 에
원본 위치를 적어 구분하고, 같은 이름이 다른 원본에서 왔으면 `_2`, `_3` 을 붙인다.

`source.txt` 에는 **저장소 루트 기준 상대경로**를 적는다. 절대경로를 적으면 폴더를
옮기거나 다른 컴퓨터로 가져갔을 때 같은 회차를 못 찾고 29분짜리 처리를 다시 돌린다.
다른 컴퓨터에서 적힌 옛 기록은 마지막 폴더명으로 맞춘다.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from src.config import MODELS_DIR, ROOT

SOURCE_FILE = "source.txt"


class ModelStore:
    def __init__(self, models_dir: Path = MODELS_DIR, root: Path = ROOT) -> None:
        self.models_dir = models_dir
        self.root = root.resolve()

    # ---- 공개 ----

    def dir_for(self, input_dir: Path, create: bool = False) -> Path:
        """이 촬영본의 산출물 폴더. 있으면 찾고, 없으면 이름을 정한다 (create 면 만든다)."""
        existing = self.find(input_dir)
        if existing is not None:
            return existing
        out = self._free_name(input_dir)
        if create:
            out.mkdir(parents=True, exist_ok=True)
            (out / SOURCE_FILE).write_text(self.encode(input_dir) + "\n", encoding="utf-8")
        return out

    def find(self, input_dir: Path) -> Path | None:
        """이 촬영본으로 이미 만든 산출물 폴더. 없으면 None."""
        if not self.models_dir.is_dir():
            return None
        for d in sorted(p for p in self.models_dir.iterdir() if p.is_dir()):
            record = d / SOURCE_FILE
            if record.is_file() and self.matches(record.read_text(encoding="utf-8"), input_dir):
                return d
        return None

    def encode(self, input_dir: Path) -> str:
        """저장할 문자열 — 루트 기준 상대경로, 구분자는 `/`. 다른 드라이브면 어쩔 수 없이 절대경로."""
        target = input_dir.resolve()
        try:
            return Path(os.path.relpath(target, self.root)).as_posix()
        except ValueError:                  # Windows 에서 드라이브가 다르다
            return target.as_posix()

    def matches(self, stored: str, input_dir: Path) -> bool:
        stored = stored.strip().replace("\\", "/")
        if not stored:
            return False
        target = input_dir.resolve()
        candidate = Path(stored)
        # `/Users/...` 는 Windows 의 Path 가 절대경로로 안 본다 — 루트에 붙이면 안 된다
        if not (candidate.is_absolute() or stored.startswith("/")):
            candidate = self.root / candidate
        if candidate.exists():
            return candidate.resolve() == target
        # 이 컴퓨터에 없는 경로 = 다른 컴퓨터에서 적힌 기록. 폴더명으로 맞춘다.
        return PurePosixPath(stored).name == target.name

    # ---- 내부 ----

    def _free_name(self, input_dir: Path) -> Path:
        base = input_dir.resolve().name or "model"
        out = self.models_dir / base
        n = 2
        while (out / SOURCE_FILE).is_file():        # 이름은 같은데 원본이 다르다
            out = self.models_dir / f"{base}_{n}"
            n += 1
        return out
