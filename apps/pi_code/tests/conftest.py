"""테스트 공통.

파이 밖(맥·윈도우)에는 picamera2·libcamera 가 없다. `tests/fakes/` 의 가짜를 먼저
import 경로에 넣어 프로그램 조립까지 검증한다. 실제 카메라 동작은 파이에서만 확인할 수 있다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKES = Path(__file__).resolve().parent / "fakes"
for p in (str(FAKES), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
