from pathlib import Path

import cv2
import numpy as np

from src.core import imgcheck
from src.core.imgcheck import CheckReport, ImageStat, check_folder, judge, list_images


def _write(path: Path, w: int = 96, h: int = 64, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    cv2.imwrite(str(path), rng.integers(0, 255, (h, w, 3), dtype=np.uint8))


def _stat(name: str, focal: float | None = None, w: int = 100, h: int = 80,
          matches: int | None = 500) -> ImageStat:
    return ImageStat(Path(name), w, h, 10.0, matches, None, focal)


def test_list_images_filters_and_sorts(tmp_path):
    for n in ("b.jpg", "a.png", "c.txt", "d.JPEG"):
        (tmp_path / n).write_bytes(b"")
    assert [p.name for p in list_images(tmp_path)] == ["a.png", "b.jpg", "d.JPEG"]
    assert list_images(tmp_path / "nope") == []


def test_check_folder_reports_stats_warnings_and_notes(tmp_path):
    for i in range(3):
        _write(tmp_path / f"{i:03d}.jpg", seed=i)
    _write(tmp_path / "003.jpg", w=50, h=50)            # 해상도가 다르다
    (tmp_path / "004.jpg").write_bytes(b"not an image")

    seen = []
    rep = check_folder(tmp_path, progress=lambda d, t: seen.append((d, t)))

    assert rep.count == 4 and len(rep.unreadable) == 1
    assert seen[-1] == (5, 5)
    assert rep.stats[0].matches is None and rep.stats[1].matches is not None
    assert not rep.ok
    assert any("장수 부족" in w for w in rep.warnings)
    assert any("해상도" in w for w in rep.warnings)
    assert any("읽지 못한" in w for w in rep.warnings)
    assert any("EXIF" in n for n in rep.notes)          # 참고이지 경고가 아니다
    assert not any("EXIF" in w for w in rep.warnings)
    assert "해상도 섞임" in rep.summary()


def test_cancel_returns_partial_report_that_is_not_ok(tmp_path):
    for i in range(2):
        _write(tmp_path / f"{i}.jpg")
    rep = check_folder(tmp_path, cancel=lambda: True)
    assert rep.cancelled and not rep.ok and rep.count == 0 and rep.warnings == []


def test_empty_folder_is_a_warning(tmp_path):
    rep = check_folder(tmp_path)
    assert rep.warnings and not rep.ok


def test_exif_rule_compares_with_readable_count_only():
    rep = CheckReport(Path("x"), stats=[_stat("a", 4.0), _stat("b", 4.0)],
                      unreadable=[Path("broken.jpg")])
    warnings, notes = imgcheck.rule_exif(rep)
    assert warnings == [] and notes == []             # 전부 있으니 깨진 파일 때문에 오경고하면 안 된다

    rep = CheckReport(Path("x"), stats=[_stat("a", 4.0), _stat("b", None)])
    warnings, _ = imgcheck.rule_exif(rep)
    assert warnings and "1/2" in warnings[0]


def test_overlap_rule_names_the_weak_images():
    rep = CheckReport(Path("x"), stats=[_stat("000.jpg", matches=None),
                                        _stat("001.jpg", matches=500),
                                        _stat("002.jpg", matches=12)])
    warnings, _ = imgcheck.rule_overlap(rep)
    assert len(warnings) == 1 and "002.jpg" in warnings[0] and "001.jpg" not in warnings[0]


def test_judge_runs_every_rule():
    rep = CheckReport(Path("x"), stats=[_stat(f"{i}.jpg", 4.0) for i in range(50)])
    assert judge(rep) == ([], [])
    calls = []
    judge(rep, rules=(lambda r: (calls.append(1) or ["w"], ["n"]),))
    assert calls == [1]
