"""학습 데이터 도구 — 앱이 아니라 **사람이 쓰는 CLI** 다.

`src/` 는 지상국 앱이고 여기는 그 앱에 넣을 `best.pt` 를 만들기까지의 준비 과정이다.
앱은 이 폴더를 import 하지 않는다. 반대로 여기서는 `src.core.detect` 의 클래스 표를
가져다 쓴다 — 라벨 이름이 앱과 어긋나면 학습한 모델이 화면에서 엉뚱하게 보인다.
"""
from __future__ import annotations

import sys


def use_utf8_stdout() -> None:
    """윈도우 콘솔 기본 인코딩(cp949)에서 한글·기호가 터지는 것을 막는다.

    `—` 하나에 UnicodeEncodeError 로 죽는다. 각 도구의 main() 이 맨 처음 부른다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
