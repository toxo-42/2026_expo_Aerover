"""탐지 클래스의 화면 표시 — 이름과 색을 한 곳에서 정한다.

영상 박스(`ui/video.py`)와 맵 핀 · 목록(`pages/detect.py`)이 같은 표를 쓴다.
탐지 코어(`core/detect.py`)는 학습 클래스 이름(casualty · vehicle · person)만 다룬다.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.ui.palette import CLASS_CASUALTY, CLASS_SAFE, CLASS_VEHICLE, TEXT_DIM


@dataclass(frozen=True)
class ClassStyle:
    name: str       # 화면에 쓰는 한글 이름
    color: str      # '#RRGGBB'

    @property
    def rgb(self) -> tuple[int, int, int]:
        """맵 핀(`viewport.set_markers`) 용 0~255 정수."""
        h = self.color.lstrip("#")
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


# 표의 순서가 목록 · 요약에 나오는 순서다 (급한 것부터).
STYLES: dict[str, ClassStyle] = {
    "casualty": ClassStyle("요구조자", CLASS_CASUALTY),
    "vehicle": ClassStyle("차량", CLASS_VEHICLE),
    "person": ClassStyle("안전", CLASS_SAFE),
}


def style(cls: str) -> ClassStyle:
    """표에 없는 클래스(다른 가중치)는 원래 이름 그대로, 회색으로."""
    return STYLES.get(cls, ClassStyle(cls, TEXT_DIM))
