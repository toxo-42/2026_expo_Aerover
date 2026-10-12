"""연사본 서브샘플.

**4장씩 묶어 각 묶음에서 가장 선명한 한 장**을 고른다.
기계적으로 4번째를 고르는 것보다 블러 중앙값이 35% 개선된다 (80.6 → 109.1).

블러는 `imgcheck.check_folder` 가 이미 잰 값을 쓴다. 다시 재지 않는다 —
446장 라플라시안에 40초가 넘게 든다.

**사본에서는 GPS EXIF 를 뺀다.** 휴대폰 사진은 기지국 위치(CELLID)를 전 장에
똑같이 박아 넣는다. OpenSfM 은 GPS 가 있으면 그 좌표로 매칭 쌍을 고르는데
(들로네 삼각분할), 점이 한 곳에 겹치면 qhull 이 죽어 ODM 이 "Cannot process
dataset" 으로 끝난다 (20261004_130739, 468장 전부 같은 좌표). 이 앱은 원래
GPS 없이 재구성하므로 빼는 게 맞다. 원본(`sessions/`)은 건드리지 않는다.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from statistics import median

import piexif

from src.core.imgcheck import ImageStat

GROUP = 4


def select(stats: list[ImageStat], group: int = GROUP) -> list[ImageStat]:
    """묶음마다 블러 최대 1장. 마지막 묶음이 짧아도 그 안에서 고른다."""
    return [max(stats[i:i + group], key=lambda s: s.blur)
            for i in range(0, len(stats), group)]


def copy_to(picked: list[ImageStat], dst: Path) -> Path:
    """고른 장을 폴더에 복사한다. 파일명은 원본 그대로 — 순서와 출처를 잃지 않는다."""
    dst.mkdir(parents=True, exist_ok=True)
    for s in picked:
        shutil.copy2(s.path, dst / s.path.name)
        strip_gps(dst / s.path.name)
    return dst


def strip_gps(path: Path) -> bool:
    """GPS IFD 만 지우고 다시 쓴다. 지웠으면 True.

    EXIF 블록만 갈아 끼우므로 JPEG 를 재압축하지 않는다 (픽셀 그대로).
    기종·초점거리는 남긴다 — ODM 이 카메라 내부값 초기값으로 쓴다.
    JPEG 가 아니거나 GPS 가 없으면 파일을 건드리지 않는다.
    """
    try:
        exif = piexif.load(str(path))
    except (piexif.InvalidImageDataError, ValueError):
        return False
    if not exif["GPS"] and piexif.ImageIFD.GPSTag not in exif["0th"]:
        return False
    exif["GPS"] = {}
    exif["0th"].pop(piexif.ImageIFD.GPSTag, None)
    piexif.insert(piexif.dump(exif), str(path))
    return True


def summary(before: list[ImageStat], after: list[ImageStat]) -> str:
    if not before or not after:
        return f"{len(before)}장 → {len(after)}장"
    return (f"{len(before)}장 → {len(after)}장 "
            f"(블러 중앙 {median(s.blur for s in before):.1f} → "
            f"{median(s.blur for s in after):.1f})")
