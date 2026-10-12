from pathlib import Path

import numpy as np
import piexif
from PIL import Image

from src.core.capture import CaptureSession
from src.core.imgcheck import ImageStat
from src.core.subsample import copy_to, select, strip_gps, summary


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


def test_select_group_one_keeps_everything_in_order(tmp_path):
    """② 건너뛰기 — 1장 묶음이면 전부 남는다 (40장 수집 회차를 그대로 ODM 에)."""
    stats = [_stat(tmp_path, f"{i:02d}.jpg", blur) for i, blur in enumerate([3, 1, 2])]
    assert select(stats, group=1) == stats


def _jpeg(path: Path, gps: bool) -> Path:
    exif = {"0th": {piexif.ImageIFD.Model: b"Galaxy Z Flip8"},
            "Exif": {piexif.ExifIFD.FocalLength: (54, 10)},
            "GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N",
                    piexif.GPSIFD.GPSLatitude: ((37, 1), (30, 1), (411588, 100000))} if gps else {}}
    rng = np.random.default_rng(0)
    Image.fromarray(rng.integers(0, 255, (32, 48, 3), dtype=np.uint8)).save(
        path, exif=piexif.dump(exif), quality=90)
    return path


def test_strip_gps_removes_only_gps_and_keeps_pixels(tmp_path):
    p = _jpeg(tmp_path / "a.jpg", gps=True)
    before = np.asarray(Image.open(p))
    assert strip_gps(p) is True
    exif = piexif.load(str(p))
    assert exif["GPS"] == {} and piexif.ImageIFD.GPSTag not in exif["0th"]
    assert exif["0th"][piexif.ImageIFD.Model] == b"Galaxy Z Flip8"
    assert exif["Exif"][piexif.ExifIFD.FocalLength] == (54, 10)
    assert (np.asarray(Image.open(p)) == before).all()      # 재압축 없음


def test_strip_gps_leaves_other_files_untouched(tmp_path):
    no_gps = _jpeg(tmp_path / "b.jpg", gps=False)
    raw = no_gps.read_bytes()
    assert strip_gps(no_gps) is False and no_gps.read_bytes() == raw
    fake = tmp_path / "c.jpg"
    fake.write_bytes(b"not a jpeg")
    assert strip_gps(fake) is False and fake.read_bytes() == b"not a jpeg"
    png = tmp_path / "d.png"
    Image.new("RGB", (4, 4)).save(png)
    assert strip_gps(png) is False


def test_copy_to_strips_gps_but_not_original(tmp_path):
    src = _jpeg(tmp_path / "e.jpg", gps=True)
    dst = copy_to([ImageStat(src, 1, 1, 1.0, None, None, None)], tmp_path / "sub")
    assert piexif.load(str(dst / "e.jpg"))["GPS"] == {}
    assert piexif.load(str(src))["GPS"]                      # 원본은 그대로
