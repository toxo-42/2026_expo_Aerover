"""파이와 지상국에 같은 내용이어야 하는 파일을 대조한다. 옆에 aerover 가 없으면 건너뛴다."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GCS = ROOT.parent / "aerover"

MIRRORED = [("src/stream/rtpjpeg.py", "src/core/rtpjpeg.py")]


@pytest.mark.parametrize("ours,theirs", MIRRORED)
def test_mirrored_files_are_identical(ours, theirs):
    other = GCS / theirs
    if not other.is_file():
        pytest.skip("aerover 가 옆에 없다")
    assert (ROOT / ours).read_bytes() == other.read_bytes(), f"{ours} 와 aerover/{theirs} 가 다르다"
