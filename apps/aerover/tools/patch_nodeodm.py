"""NodeODM 컨테이너가 `--cameras` JSON 을 깨뜨리지 않게 고친다.

NodeODM 3.x (`opendronemap/nodeodm:gpu` 3.6.2 확인) 의 `libs/odmRunner.js` 는 옵션 값을
`"..."` 로 감싸기 전에 **값 안의 `"` 를 전부 지운다.** 파이캠 수집본은 EXIF 가 없어 카메라
내부값을 `--cameras` JSON 으로 주는데 (core/odm.py), 따옴표가 빠지면 ODM 이 시작하자마자
`is not a valid JSON string` 으로 죽고 앱에는 `TaskFailedError` 로 뜬다. NodeODM 이 옵션 값을
JSON 으로 먼저 검사하므로 파일 경로로 우회할 수도 없다.

작은따옴표로 감싸는 방식으로 바꾼다 — 셸이 아무것도 펼치지 않으니 명령 주입도 막힌다.
패치는 컨테이너 안에만 남는다. **컨테이너를 새로 만들면 다시 돌려야 한다.**

    uv run python -m tools.patch_nodeodm            # 컨테이너 이름 nodeodm
    uv run python -m tools.patch_nodeodm nodeodm_test
"""
from __future__ import annotations

import subprocess
import sys

FILE = "/var/www/libs/odmRunner.js"
MARK = "// aerover patch"
OLD = 'const shQuote = s =>  {\n    s = String(s).replace(/"/g, "")\n    return `"${s}"`;\n}'
NEW = (MARK + ": keep JSON quotes (--cameras). Single quotes keep it injection-safe.\n"
       "const shQuote = s => \"'\" + String(s).replace(/'/g, `'\"'\"'`) + \"'\";")


def main(container: str = "nodeodm") -> int:
    src = subprocess.run(["docker", "exec", container, "cat", FILE],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    if MARK in src:
        print("이미 패치됨")
        return 0
    if OLD not in src:
        print(f"{FILE} 에서 원래 shQuote 를 못 찾았다 — NodeODM 버전이 달라 직접 확인해야 한다")
        return 1
    subprocess.run(["docker", "exec", "-i", "-u", "root", container, "sh", "-c", f"cat > {FILE}"],
                   input=src.replace(OLD, NEW), text=True, encoding="utf-8", check=True)
    subprocess.run(["docker", "restart", container], check=True)
    print("패치 후 재시작함")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
