"""연사본 서브샘플.

**4장씩 묶어 각 묶음에서 가장 선명한 한 장**을 고른다.
기계적으로 4번째를 고르는 것보다 블러 중앙값이 35% 개선된다 (80.6 → 109.1).

블러는 `imgcheck.check_folder` 가 이미 잰 값을 쓴다. 다시 재지 않는다 —
446장 라플라시안에 40초가 넘게 든다.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from statistics import median

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
    return dst


def summary(before: list[ImageStat], after: list[ImageStat]) -> str:
    if not before or not after:
        return f"{len(before)}장 → {len(after)}장"
    return (f"{len(before)}장 → {len(after)}장 "
            f"(블러 중앙 {median(s.blur for s in before):.1f} → "
            f"{median(s.blur for s in after):.1f})")
