from pathlib import Path

from src.core.capture import CaptureSession
from src.core.imgcheck import ImageStat
from src.core.subsample import copy_to, select, summary


def test_capture_respects_interval_and_target(tmp_path):
    s = CaptureSession(tmp_path, interval=3.0, target=2)
    assert s.dir.parent == tmp_path and s.dir.is_dir()
    assert s.offer(b"f0", now=100.0) is True          # 첫 프레임은 바로
    assert s.offer(b"f1", now=101.0) is False         # 간격 미달
    assert not s.done
    assert s.offer(b"f2", now=103.0) is True
    assert s.done
    assert sorted(p.name for p in s.dir.iterdir()) == ["000.jpg", "001.jpg"]
    assert (s.dir / "001.jpg").read_bytes() == b"f2"  # 재인코딩 없이 원본 그대로


def test_capture_target_zero_never_finishes(tmp_path):
    s = CaptureSession(tmp_path, interval=0.0, target=0)
    for i in range(5):
        s.offer(b"x", now=float(i))
    assert s.saved == 5 and not s.done


def _stat(tmp_path: Path, name: str, blur: float) -> ImageStat:
    p = tmp_path / name
    p.write_bytes(name.encode())
    return ImageStat(p, 1, 1, blur, None, None, None)


def test_select_keeps_sharpest_per_group_and_copies(tmp_path):
    stats = [_stat(tmp_path, f"{i:02d}.jpg", blur) for i, blur in
             enumerate([1, 9, 3, 2, 5, 4, 8, 7, 6])]        # 4+4+1
    picked = select(stats)
    assert [s.path.name for s in picked] == ["01.jpg", "06.jpg", "08.jpg"]
    dst = copy_to(picked, tmp_path / "sub")
    assert sorted(p.name for p in dst.iterdir()) == ["01.jpg", "06.jpg", "08.jpg"]
    assert (dst / "06.jpg").read_bytes() == b"06.jpg"
    assert summary(stats, picked).startswith("9장 → 3장")
    assert summary([], []) == "0장 → 0장"
