"""YOLO 라벨 파일 읽기·쓰기 — Qt 를 모르는 순수 로직이라 바이트만으로 테스트한다.

상자는 **이미지 픽셀 좌표**로 다룬다 (x1, y1, x2, y2). 파일에 적을 때만 0~1 로
정규화한다 — 화면에서 그리고 옮기는 동안 정규화된 값을 들고 있으면 반올림 오차가 쌓인다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Box:
    cls: int
    x1: float
    y1: float
    x2: float
    y2: float

    def normalized(self) -> "Box":
        """좌상단이 항상 (x1, y1) 이도록 바로잡는다. 반대로 드래그해도 같은 상자다."""
        return Box(self.cls, min(self.x1, self.x2), min(self.y1, self.y2),
                   max(self.x1, self.x2), max(self.y1, self.y2))

    def contains(self, x: float, y: float) -> bool:
        b = self.normalized()
        return b.x1 <= x <= b.x2 and b.y1 <= y <= b.y2

    @property
    def area(self) -> float:
        b = self.normalized()
        return (b.x2 - b.x1) * (b.y2 - b.y1)


def load(path: Path, width: int, height: int) -> list[Box]:
    """YOLO 텍스트 -> 픽셀 좌표 상자. 파일이 없으면 빈 목록 (아직 라벨 안 단 장)."""
    if not path.is_file():
        return []
    boxes: list[Box] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue                        # 빈 줄이나 깨진 줄은 조용히 버린다
        cls = int(parts[0])
        xc, yc, w, h = (float(v) for v in parts[1:])
        boxes.append(Box(cls, (xc - w / 2) * width, (yc - h / 2) * height,
                         (xc + w / 2) * width, (yc + h / 2) * height))
    return boxes


def dump(boxes: list[Box], width: int, height: int) -> str:
    """픽셀 좌표 상자 -> YOLO 텍스트. 이미지 밖으로 나간 상자는 가장자리에 맞춘다."""
    lines = []
    for box in boxes:
        b = box.normalized()
        x1, y1 = max(0.0, b.x1), max(0.0, b.y1)
        x2, y2 = min(float(width), b.x2), min(float(height), b.y2)
        if x2 - x1 < 1 or y2 - y1 < 1:
            continue                        # 화면 밖으로 완전히 나간 상자는 버린다
        lines.append(f"{b.cls} {(x1 + x2) / 2 / width:.6f} {(y1 + y2) / 2 / height:.6f} "
                     f"{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}")
    return "\n".join(lines) + ("\n" if lines else "")


def save(path: Path, boxes: list[Box], width: int, height: int) -> None:
    path.write_text(dump(boxes, width, height), encoding="utf-8")


def pick(boxes: list[Box], x: float, y: float) -> int | None:
    """(x, y) 를 품은 상자 중 **가장 작은 것**의 인덱스. 없으면 None.

    큰 상자가 작은 상자를 덮고 있을 때 작은 쪽을 집어야 한다 — 잔해를 통째로 잡은
    큰 오탐 위에 사람 상자가 겹쳐 있는 경우가 흔하다.
    """
    hits = [i for i, b in enumerate(boxes) if b.contains(x, y)]
    return min(hits, key=lambda i: boxes[i].area) if hits else None


def zoom_at(offset: tuple[float, float], zoom: float, new_zoom: float,
            cursor: tuple[float, float]) -> tuple[float, float]:
    """확대해도 **커서 밑 지점이 제자리에 있도록** 하는 새 offset.

    이것이 없으면 휠을 굴릴 때마다 보던 곳이 화면 밖으로 달아난다. 모형이 작아
    크게 확대해야 하므로 이 성질이 없으면 못 쓴다.
    """
    image_x = (cursor[0] - offset[0]) / zoom
    image_y = (cursor[1] - offset[1]) / zoom
    return cursor[0] - image_x * new_zoom, cursor[1] - image_y * new_zoom
