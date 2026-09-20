"""검사 결과 캐시 — 저장·복원과 낡음 판정."""
from __future__ import annotations

import os
from pathlib import Path

from src.core import checkcache
from src.core.imgcheck import CheckReport, ImageStat


def _folder(tmp_path: Path, names=("a.jpg", "b.jpg")) -> Path:
    folder = tmp_path / "shots"
    folder.mkdir()
    for i, n in enumerate(names):
        (folder / n).write_bytes(b"x" * (10 + i))
    return folder


def _report(folder: Path) -> CheckReport:
    return CheckReport(
        folder=folder,
        stats=[ImageStat(path=p, width=4000, height=3000, blur=120.5,
                         matches=None if i == 0 else 300, model="IMX477", focal=6.0)
               for i, p in enumerate(sorted(folder.iterdir()))],
        notes=["EXIF 없음"],
    )


def test_저장한_보고서를_그대로_복원한다(tmp_path):
    folder = _folder(tmp_path)
    dest = tmp_path / "out"
    checkcache.save(_report(folder), dest)

    loaded = checkcache.load(folder, dest)
    assert loaded is not None
    assert loaded.count == 2
    assert loaded.blurs == [120.5, 120.5]
    assert loaded.stats[0].matches is None and loaded.stats[1].matches == 300
    assert loaded.notes == ["EXIF 없음"]
    assert loaded.ok          # 경고가 없으면 통과 상태도 살아난다


def test_사진이_바뀌면_캐시를_버린다(tmp_path):
    folder = _folder(tmp_path)
    dest = tmp_path / "out"
    checkcache.save(_report(folder), dest)

    # 장수는 그대로인데 내용만 바뀐 경우 — 장수만 보면 놓친다.
    target = folder / "a.jpg"
    target.write_bytes(b"y" * 99)
    os.utime(target, (0, 0))
    assert checkcache.load(folder, dest) is None


def test_장이_늘면_캐시를_버린다(tmp_path):
    folder = _folder(tmp_path)
    dest = tmp_path / "out"
    checkcache.save(_report(folder), dest)

    (folder / "c.jpg").write_bytes(b"z" * 10)
    assert checkcache.load(folder, dest) is None


def test_취소된_보고서는_저장하지_않는다(tmp_path):
    folder = _folder(tmp_path)
    dest = tmp_path / "out"
    report = _report(folder)
    report.cancelled = True

    assert checkcache.save(report, dest) is None
    assert checkcache.load(folder, dest) is None
